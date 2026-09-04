"""Animal / non-connected actor conflict detector per Playbook 10.

Models dynamic actors (animals, unconnected pedestrians) as expanding
reachable regions with class-specific speed/turn uncertainty. Produces
RiskEvents when those regions intersect road or vehicle reachable
regions.

Key design choices
------------------
* Generic DynamicActor handling: position, velocity, behaviour model,
  uncertainty envelope, and observation lifecycle are all tracked.
* Reachable region expands using configurable max speed and turn
  uncertainty per animal class. Unknown classes use a conservative
  default.
* When an observation disappears, the track continues with decaying
  confidence for ``track_prediction_timeout_s``; it then expires cleanly
  rather than being teleport-removed.
* A single low-confidence detection never triggers a fleet-wide critical
  alert (``low_confidence_alert_suppression`` caps severity).
* Animal trajectory parallel to the road produces lower risk than one
  entering the lane (crossing angle factor).
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timezone
from typing import Any

from packages.geo.helpers import (
    bearing_difference,
    haversine_distance,
    point_along_bearing,
)
from packages.safety_policies.base import SafetyDetector
from packages.safety_policies.config import PolicyConfig
from packages.schemas.canonical import RiskEvent, RiskType

logger = logging.getLogger(__name__)

_DETECTOR_VERSION = "0.1.0"


class AnimalConflictDetector(SafetyDetector):
    """Detect conflict risk from animals and non-connected dynamic actors."""

    def __init__(self, config: PolicyConfig) -> None:
        self._cfg = config.animal_conflict
        # Persistent track state.
        # Key: track_id (or observation_id), Value: _TrackState dict.
        self._tracks: dict[str, dict[str, Any]] = {}

    # -- SafetyDetector protocol -----------------------------------------

    @property
    def name(self) -> str:
        return "animal_conflict"

    @property
    def risk_type(self) -> RiskType:
        return RiskType.ANIMAL_CROSSING

    @property
    def version(self) -> str:
        return _DETECTOR_VERSION

    # -- public API ------------------------------------------------------

    def evaluate(self, world_state: dict[str, Any]) -> list[RiskEvent]:
        now = datetime.now(timezone.utc)

        vehicles = world_state.get("vehicles", [])
        observations = world_state.get("dynamic_actors", [])
        road_network = world_state.get("road_network", {})
        segments = road_network.get("segments", [])
        segments_by_id = _index_segments(segments)

        # Phase 1 -- update / create / predict tracks.
        self._update_tracks(observations, now)
        self._predict_missing_tracks(now)
        self._expire_tracks(now)

        if not self._tracks or not vehicles:
            return []

        risk_events: list[RiskEvent] = []

        for track_id, track in self._tracks.items():
            if track["confidence"] < self._cfg.min_detector_confidence:
                continue

            actor_class = track["actor_class"]
            max_speed = self._max_speed_for(actor_class)
            animal_pos = track["position"]
            animal_speed = track.get("speed_mps") or 0.0
            animal_heading = track.get("heading_deg")
            dt_since_obs = (now - track["last_observed"]).total_seconds()

            # Build reachable region radius for the animal.
            reachable_radius = _reachable_radius(
                animal_speed, max_speed, dt_since_obs,
                self._cfg.turn_uncertainty_deg,
            )

            for vehicle in vehicles:
                v_pos = vehicle.get("position", {})
                v_lat = v_pos.get("lat")
                v_lon = v_pos.get("lon")
                a_lat = animal_pos.get("lat")
                a_lon = animal_pos.get("lon")
                if v_lat is None or v_lon is None or a_lat is None or a_lon is None:
                    continue

                dist = haversine_distance(a_lat, a_lon, v_lat, v_lon)
                v_speed = vehicle.get("speed_mps", 0.0)

                # Vehicle reachable in the next few seconds.
                v_reachable = v_speed * 5.0  # 5-second horizon

                gap = dist - reachable_radius - v_reachable
                if gap > 0:
                    continue

                # Regions intersect -- evaluate risk.
                crossing_angle = _crossing_angle(
                    animal_heading, animal_pos,
                    vehicle.get("heading_deg", 0.0), v_pos, segments_by_id,
                    vehicle.get("road_segment_id"),
                )

                severity = _compute_severity(
                    dist, reachable_radius, v_speed, crossing_angle,
                    track["confidence"],
                )

                # Enforce low-confidence suppression: a single low-confidence
                # detection must not produce a critical-level severity.
                if track["confidence"] < self._cfg.low_confidence_alert_suppression:
                    severity = min(severity, 0.55)

                confidence = track["confidence"]

                ttc = dist / (v_speed + animal_speed) if (v_speed + animal_speed) > 0 else None

                v_id = vehicle.get("actor_id", "unknown")

                evidence = [
                    {
                        "type": "animal_conflict",
                        "track_id": track_id,
                        "actor_class": actor_class,
                        "animal_position": animal_pos,
                        "animal_speed_mps": round(animal_speed, 2),
                        "animal_heading_deg": round(animal_heading, 1) if animal_heading is not None else None,
                        "reachable_radius_m": round(reachable_radius, 1),
                        "vehicle_id": v_id,
                        "vehicle_distance_m": round(dist, 1),
                        "vehicle_speed_mps": round(v_speed, 2),
                        "crossing_angle_deg": round(crossing_angle, 1),
                        "observation_age_s": round(dt_since_obs, 2),
                        "detector_confidence": round(track["confidence"], 3),
                        "is_predicted": track.get("is_predicted", False),
                    },
                ]

                risk_events.append(
                    self.create_risk_event(
                        affected_actor_ids=[v_id],
                        severity=severity,
                        confidence=confidence,
                        time_to_conflict_s=ttc,
                        min_predicted_distance_m=max(0.0, dist - reachable_radius),
                        evidence=evidence,
                        road_segment_id=vehicle.get("road_segment_id"),
                    )
                )

        return risk_events

    # -- track lifecycle -------------------------------------------------

    def _update_tracks(
        self,
        observations: list[dict[str, Any]],
        now: datetime,
    ) -> None:
        """Ingest fresh observations into the track state."""
        seen_track_ids: set[str] = set()

        for obs in observations:
            det_conf = obs.get("detector_confidence", 0.0)
            if det_conf < self._cfg.min_detector_confidence:
                continue

            track_id = obs.get("track_id") or obs.get("observation_id", "")
            if not track_id:
                continue

            seen_track_ids.add(track_id)

            ts_raw = obs.get("ts")
            if isinstance(ts_raw, str):
                try:
                    ts = datetime.fromisoformat(ts_raw)
                except ValueError:
                    ts = now
            elif isinstance(ts_raw, datetime):
                ts = ts_raw
            else:
                ts = now

            self._tracks[track_id] = {
                "actor_class": obs.get("actor_class", "unknown"),
                "position": {
                    "lat": obs.get("position", {}).get("lat"),
                    "lon": obs.get("position", {}).get("lon"),
                },
                "speed_mps": obs.get("speed_mps"),
                "heading_deg": obs.get("heading_deg"),
                "confidence": det_conf,
                "last_observed": ts,
                "is_predicted": False,
                "source_id": obs.get("source_id", ""),
            }

    def _predict_missing_tracks(self, now: datetime) -> None:
        """Continue short-term prediction for tracks no longer observed."""
        for track_id, track in self._tracks.items():
            if track["is_predicted"]:
                # Already predicted -- update position further.
                pass
            elif (now - track["last_observed"]).total_seconds() <= 0.5:
                # Still fresh, no prediction needed.
                continue

            dt = (now - track["last_observed"]).total_seconds()
            if dt <= 0:
                continue

            # Mark as predicted and decay confidence.
            track["is_predicted"] = True
            decay_rate = 1.0 / max(1.0, self._cfg.track_prediction_timeout_s)
            track["confidence"] = max(
                0.0,
                track["confidence"] * math.exp(-decay_rate * dt),
            )

            # Project position forward if we have heading and speed.
            speed = track.get("speed_mps")
            heading = track.get("heading_deg")
            pos = track.get("position", {})
            if speed and heading is not None and pos.get("lat") is not None:
                new_lat, new_lon = point_along_bearing(
                    pos["lat"], pos["lon"], heading, speed * dt,
                )
                track["position"] = {"lat": new_lat, "lon": new_lon}

    def _expire_tracks(self, now: datetime) -> None:
        """Remove tracks that have exceeded the prediction timeout."""
        timeout = self._cfg.track_prediction_timeout_s
        expired = [
            tid for tid, t in self._tracks.items()
            if (now - t["last_observed"]).total_seconds() > timeout
        ]
        for tid in expired:
            logger.debug("Track %s expired after prediction timeout", tid)
            del self._tracks[tid]

    # -- helpers ---------------------------------------------------------

    def _max_speed_for(self, actor_class: str) -> float:
        speeds = self._cfg.max_animal_speed_mps
        return speeds.get(actor_class, speeds.get("default", 8.0))


# -- module-level helpers ------------------------------------------------


def _index_segments(segments: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {s["segment_id"]: s for s in segments if "segment_id" in s}


def _reachable_radius(
    current_speed: float,
    max_speed: float,
    dt_s: float,
    turn_uncertainty_deg: float,
) -> float:
    """Compute the radius of the reachable region for a dynamic actor.

    The animal could accelerate to max_speed and turn within the
    uncertainty cone, so the reachable region is a sector that we
    approximate as a circle of the maximum reachable distance.
    """
    effective_speed = max(current_speed, max_speed * 0.5)
    # Acceleration allowance: could reach max_speed from current speed.
    avg_speed = (effective_speed + max_speed) / 2.0
    linear_reach = avg_speed * dt_s

    # Wider turn uncertainty expands the effective radius.
    turn_factor = 1.0 + (turn_uncertainty_deg / 360.0)
    return linear_reach * turn_factor


def _crossing_angle(
    animal_heading: float | None,
    animal_pos: dict[str, Any],
    vehicle_heading: float,
    vehicle_pos: dict[str, Any],
    segments_by_id: dict[str, dict[str, Any]],
    vehicle_segment_id: str | None,
) -> float:
    """Compute the angle at which the animal's trajectory crosses the road.

    Returns 0-90 degrees: 90 = perpendicular (entering lane), 0 = parallel.
    """
    if animal_heading is None:
        # Unknown heading -- conservative mid-range angle.
        return 45.0

    # Determine road direction from segment metadata or vehicle heading.
    road_heading = vehicle_heading
    if vehicle_segment_id:
        seg = segments_by_id.get(vehicle_segment_id)
        if seg and "direction_deg" in seg:
            road_heading = seg["direction_deg"]

    raw_diff = abs(bearing_difference(animal_heading, road_heading))
    # Normalize to 0-90: perpendicular is worst.
    if raw_diff > 90:
        raw_diff = 180 - raw_diff
    return raw_diff


def _compute_severity(
    distance_m: float,
    reachable_radius_m: float,
    vehicle_speed_mps: float,
    crossing_angle_deg: float,
    detector_confidence: float,
) -> float:
    """Severity from proximity, vehicle speed, and crossing geometry."""
    # Proximity factor: how deep into overlap.
    if reachable_radius_m > 0:
        overlap_depth = max(0.0, reachable_radius_m - distance_m) / reachable_radius_m
    else:
        overlap_depth = 0.0
    proximity_factor = min(1.0, 0.3 + 0.4 * overlap_depth)

    # Speed factor.
    speed_factor = min(1.0, vehicle_speed_mps / 25.0)

    # Crossing angle factor: perpendicular (90 deg) is highest risk.
    # Parallel (0 deg) is lower.
    angle_factor = crossing_angle_deg / 90.0

    base = 0.2 * proximity_factor + 0.3 * speed_factor + 0.3 * angle_factor
    # Scale by confidence so low-confidence detections produce lower severity.
    return min(1.0, max(0.0, base * (0.5 + 0.5 * detector_confidence)))
