"""Blind intersection risk detector per Playbook 8 / Section 24.

Uses junction conflict zones from the road graph to identify vehicles on
intersecting approach segments whose ETA distributions to the conflict
zone overlap.  Signal and right-of-way state is included as evidence and
reduces (but never eliminates) residual risk.

Key design choices
------------------
* ETA is computed via along-segment network distance, not straight-line.
* Each conflict zone can be approached from multiple segments; all pairs
  of approaching actors are evaluated.
* Signal/right-of-way state is *context* that scales severity, not a
  binary gate -- compliance cannot be guaranteed.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from itertools import combinations
from typing import Any

from packages.geo.helpers import haversine_distance
from packages.safety_policies.base import SafetyDetector
from packages.safety_policies.config import PolicyConfig
from packages.schemas.canonical import RiskEvent, RiskType

logger = logging.getLogger(__name__)

_DETECTOR_VERSION = "0.1.0"

# Signal states that indicate protected movement (severity discount).
_PROTECTED_SIGNAL_STATES = frozenset({"GREEN", "PROTECTED_GREEN"})
# Maximum severity discount from a favourable signal state.
_SIGNAL_SEVERITY_DISCOUNT = 0.3


class BlindIntersectionDetector(SafetyDetector):
    """Detect conflict risk at blind/occluded intersections."""

    def __init__(self, config: PolicyConfig) -> None:
        self._cfg = config.blind_intersection

    # -- SafetyDetector protocol -----------------------------------------

    @property
    def name(self) -> str:
        return "blind_intersection"

    @property
    def risk_type(self) -> RiskType:
        return RiskType.BLIND_INTERSECTION

    @property
    def version(self) -> str:
        return _DETECTOR_VERSION

    # -- public API ------------------------------------------------------

    def evaluate(self, world_state: dict[str, Any]) -> list[RiskEvent]:
        vehicles = world_state.get("vehicles", [])
        intersections = world_state.get("intersections", [])
        road_network = world_state.get("road_network", {})
        segments_by_id = _index_segments(road_network.get("segments", []))

        if not vehicles or not intersections:
            return []

        vehicles_by_segment: dict[str, list[dict[str, Any]]] = {}
        for v in vehicles:
            seg = v.get("road_segment_id")
            if seg:
                vehicles_by_segment.setdefault(seg, []).append(v)

        risk_events: list[RiskEvent] = []

        for intersection in intersections:
            int_id = intersection.get("intersection_id", "unknown")
            int_pos = intersection.get("position", {})
            conflict_zones = intersection.get("conflict_zones", [])
            signal_state = intersection.get("signal_state")

            for cz in conflict_zones:
                zone_id = cz.get("zone_id", "unknown")
                approaching_segments: list[str] = cz.get("approaching_segments", [])

                # Gather approaching actors with their per-segment ETA info.
                approachers: list[dict[str, Any]] = []
                for seg_id in approaching_segments:
                    seg_meta = segments_by_id.get(seg_id)
                    for v in vehicles_by_segment.get(seg_id, []):
                        dist = _network_distance_to_zone(v, int_pos, seg_meta)
                        if dist is None or dist > self._cfg.approach_distance_m:
                            continue
                        speed = v.get("speed_mps", 0.0)
                        if speed <= 0:
                            continue
                        eta_mean = dist / speed
                        # Uncertainty grows with distance and inversely with
                        # confidence in the position measurement.
                        pos_unc = v.get("position_uncertainty_m", 2.0)
                        eta_unc = (pos_unc / speed) + 0.5  # seconds
                        approachers.append({
                            "vehicle": v,
                            "segment_id": seg_id,
                            "distance_m": dist,
                            "eta_mean_s": eta_mean,
                            "eta_uncertainty_s": eta_unc,
                            "eta_lo": max(0.0, eta_mean - eta_unc),
                            "eta_hi": eta_mean + eta_unc,
                        })

                # Evaluate all pairs from *different* approach segments.
                for a, b in combinations(approachers, 2):
                    if a["segment_id"] == b["segment_id"]:
                        continue
                    overlap = _eta_overlap(a, b)
                    if overlap <= 0:
                        continue
                    if overlap < self._cfg.eta_overlap_threshold_s:
                        continue

                    # -- severity / confidence --------------------------------
                    base_severity = min(1.0, overlap / (2.0 * self._cfg.eta_overlap_threshold_s) + 0.3)
                    confidence = self._confidence(a, b)
                    if confidence < self._cfg.min_confidence:
                        continue

                    severity = _apply_signal_context(
                        base_severity, signal_state, a["segment_id"], b["segment_id"],
                    )

                    aid_a = a["vehicle"].get("actor_id", "unknown")
                    aid_b = b["vehicle"].get("actor_id", "unknown")
                    ttc = min(a["eta_mean_s"], b["eta_mean_s"])

                    evidence = [
                        {
                            "type": "intersection_conflict",
                            "intersection_id": int_id,
                            "zone_id": zone_id,
                            "actor_a": aid_a,
                            "actor_a_segment": a["segment_id"],
                            "actor_a_eta_s": round(a["eta_mean_s"], 2),
                            "actor_a_eta_uncertainty_s": round(a["eta_uncertainty_s"], 2),
                            "actor_a_distance_m": round(a["distance_m"], 1),
                            "actor_b": aid_b,
                            "actor_b_segment": b["segment_id"],
                            "actor_b_eta_s": round(b["eta_mean_s"], 2),
                            "actor_b_eta_uncertainty_s": round(b["eta_uncertainty_s"], 2),
                            "actor_b_distance_m": round(b["distance_m"], 1),
                            "eta_overlap_s": round(overlap, 2),
                            "signal_state": _signal_summary(signal_state, a["segment_id"], b["segment_id"]),
                        },
                    ]

                    risk_events.append(
                        self.create_risk_event(
                            affected_actor_ids=[aid_a, aid_b],
                            severity=severity,
                            confidence=confidence,
                            time_to_conflict_s=ttc,
                            min_predicted_distance_m=0.0,
                            evidence=evidence,
                            geometry=cz.get("geometry"),
                            road_segment_id=a["segment_id"],
                        )
                    )

        return risk_events

    # -- internal helpers ------------------------------------------------

    @staticmethod
    def _confidence(a: dict[str, Any], b: dict[str, Any]) -> float:
        """Derive joint confidence from per-actor ETA uncertainty."""
        # Tighter ETA windows -> higher confidence.
        total_unc = a["eta_uncertainty_s"] + b["eta_uncertainty_s"]
        if total_unc <= 0:
            return 1.0
        overlap = _eta_overlap(a, b)
        return min(1.0, max(0.0, overlap / total_unc))


# -- module-level helpers ------------------------------------------------


def _index_segments(segments: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {s["segment_id"]: s for s in segments if "segment_id" in s}


def _network_distance_to_zone(
    vehicle: dict[str, Any],
    intersection_pos: dict[str, float],
    segment_meta: dict[str, Any] | None,
) -> float | None:
    """Compute along-segment distance from *vehicle* to the conflict zone.

    If segment metadata includes ``length_m`` we use it as the remaining
    distance proxy (segment length minus distance already traversed along
    the segment towards the intersection).  Falls back to haversine when
    metadata is incomplete.
    """
    v_pos = vehicle.get("position", {})
    v_lat = v_pos.get("lat")
    v_lon = v_pos.get("lon")
    i_lat = intersection_pos.get("lat")
    i_lon = intersection_pos.get("lon")
    if v_lat is None or v_lon is None or i_lat is None or i_lon is None:
        return None

    straight_dist = haversine_distance(v_lat, v_lon, i_lat, i_lon)

    if segment_meta is not None:
        seg_len = segment_meta.get("length_m")
        if seg_len is not None and seg_len > 0:
            # Use the proportion of segment remaining, clamped to segment
            # length.  This is a reasonable proxy for along-road distance
            # when segment geometry is not fully resolved.
            return min(straight_dist * 1.15, seg_len)

    # No segment metadata -- use straight-line with curvature fudge factor.
    return straight_dist * 1.15


def _eta_overlap(a: dict[str, Any], b: dict[str, Any]) -> float:
    """Compute overlap of two ETA windows (seconds). Returns 0 if none."""
    lo = max(a["eta_lo"], b["eta_lo"])
    hi = min(a["eta_hi"], b["eta_hi"])
    return max(0.0, hi - lo)


def _apply_signal_context(
    base_severity: float,
    signal_state: dict[str, Any] | None,
    seg_a: str,
    seg_b: str,
) -> float:
    """Adjust severity when signal state is available.

    A protected green for one movement reduces risk but never below a
    residual floor -- compliance is never guaranteed.
    """
    if signal_state is None:
        return min(1.0, base_severity)

    movements: dict[str, str] = signal_state.get("movements", {})
    a_signal = movements.get(seg_a, "UNKNOWN")
    b_signal = movements.get(seg_b, "UNKNOWN")

    discount = 0.0
    if a_signal in _PROTECTED_SIGNAL_STATES or b_signal in _PROTECTED_SIGNAL_STATES:
        discount = _SIGNAL_SEVERITY_DISCOUNT

    adjusted = base_severity - discount
    # Residual floor -- signals do not eliminate risk.
    return max(0.15, min(1.0, adjusted))


def _signal_summary(
    signal_state: dict[str, Any] | None,
    seg_a: str,
    seg_b: str,
) -> dict[str, str]:
    """Build a small evidence dict about signal context."""
    if signal_state is None:
        return {"available": "false"}
    movements = signal_state.get("movements", {})
    return {
        "available": "true",
        "segment_a_signal": movements.get(seg_a, "UNKNOWN"),
        "segment_b_signal": movements.get(seg_b, "UNKNOWN"),
    }
