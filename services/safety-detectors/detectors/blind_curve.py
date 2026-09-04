"""Blind curve risk detector per Playbook 8 / Section 24.

Identifies actors on connected opposing or merging road segments beyond
local sight-line assumptions that are closing on a shared curve.

Key design choices
------------------
* Risk is evaluated via along-road *network distance*, not straight-line
  distance. Connected segments form the path.
* Visibility metadata from segment data is used when available but its
  absence does not prevent basic trajectory risk assessment.
* Curvature of segments along the path must exceed the configured
  threshold for the situation to qualify as a "blind" curve.
* Opposing and merging trajectories are distinguished: opposing traffic
  produces higher base severity.
"""

from __future__ import annotations

import logging
from typing import Any

from packages.geo.helpers import bearing_difference, haversine_distance
from packages.safety_policies.base import SafetyDetector
from packages.safety_policies.config import PolicyConfig
from packages.schemas.canonical import RiskEvent, RiskType

logger = logging.getLogger(__name__)

_DETECTOR_VERSION = "0.1.0"

# Heading difference (absolute) above which two actors are considered
# opposing rather than merging.
_OPPOSING_HEADING_THRESHOLD_DEG = 120.0


class BlindCurveDetector(SafetyDetector):
    """Detect conflict risk around blind / high-curvature road curves."""

    def __init__(self, config: PolicyConfig) -> None:
        self._cfg = config.blind_curve

    # -- SafetyDetector protocol -----------------------------------------

    @property
    def name(self) -> str:
        return "blind_curve"

    @property
    def risk_type(self) -> RiskType:
        return RiskType.BLIND_CURVE

    @property
    def version(self) -> str:
        return _DETECTOR_VERSION

    # -- public API ------------------------------------------------------

    def evaluate(self, world_state: dict[str, Any]) -> list[RiskEvent]:
        vehicles = world_state.get("vehicles", [])
        road_network = world_state.get("road_network", {})
        segments = road_network.get("segments", [])

        if not vehicles or not segments:
            return []

        seg_index = _index_segments(segments)
        vehicles_by_segment = _group_vehicles_by_segment(vehicles)

        risk_events: list[RiskEvent] = []
        evaluated_pairs: set[tuple[str, str]] = set()

        for vehicle in vehicles:
            v_seg_id = vehicle.get("road_segment_id")
            if not v_seg_id:
                continue
            v_seg = seg_index.get(v_seg_id)
            if v_seg is None:
                continue

            speed_a = vehicle.get("speed_mps", 0.0)
            if speed_a <= 0:
                continue

            # Walk connected segments up to network distance threshold.
            reachable = _reachable_segments(
                v_seg_id, seg_index, self._cfg.network_distance_threshold_m,
            )

            for other_seg_id, path_distance, path_curvature in reachable:
                if path_curvature < self._cfg.curvature_threshold_deg:
                    continue

                for other_v in vehicles_by_segment.get(other_seg_id, []):
                    aid_a = vehicle.get("actor_id", "unknown")
                    aid_b = other_v.get("actor_id", "unknown")
                    if aid_a == aid_b:
                        continue
                    pair_key = tuple(sorted((aid_a, aid_b)))
                    if pair_key in evaluated_pairs:
                        continue
                    evaluated_pairs.add(pair_key)

                    speed_b = other_v.get("speed_mps", 0.0)
                    heading_a = vehicle.get("heading_deg", 0.0)
                    heading_b = other_v.get("heading_deg", 0.0)

                    closing_speed = _closing_speed(
                        speed_a, speed_b, heading_a, heading_b,
                    )
                    if closing_speed < self._cfg.min_closing_speed_mps:
                        continue

                    # Network distance between the two actors along the path.
                    actor_net_dist = _actor_network_distance(
                        vehicle, other_v, path_distance, seg_index, v_seg_id, other_seg_id,
                    )

                    if closing_speed > 0:
                        ttc = actor_net_dist / closing_speed
                    else:
                        ttc = float("inf")

                    is_opposing = abs(bearing_difference(heading_a, heading_b)) >= _OPPOSING_HEADING_THRESHOLD_DEG

                    # Visibility check -- use metadata when available.
                    visibility_limited = _visibility_limited(
                        v_seg, seg_index.get(other_seg_id), actor_net_dist,
                    )

                    severity = _compute_severity(
                        closing_speed, actor_net_dist, is_opposing,
                        visibility_limited, self._cfg.network_distance_threshold_m,
                    )
                    confidence = _compute_confidence(
                        vehicle, other_v, visibility_limited,
                    )

                    evidence = [
                        {
                            "type": "blind_curve_conflict",
                            "actor_a": aid_a,
                            "actor_a_segment": v_seg_id,
                            "actor_a_speed_mps": round(speed_a, 2),
                            "actor_b": aid_b,
                            "actor_b_segment": other_seg_id,
                            "actor_b_speed_mps": round(speed_b, 2),
                            "closing_speed_mps": round(closing_speed, 2),
                            "network_distance_m": round(actor_net_dist, 1),
                            "path_curvature_deg": round(path_curvature, 1),
                            "is_opposing": is_opposing,
                            "visibility_limited": visibility_limited,
                            "time_to_meeting_s": round(ttc, 2) if ttc != float("inf") else None,
                        },
                    ]

                    risk_events.append(
                        self.create_risk_event(
                            affected_actor_ids=[aid_a, aid_b],
                            severity=severity,
                            confidence=confidence,
                            time_to_conflict_s=ttc if ttc != float("inf") else None,
                            min_predicted_distance_m=0.0,
                            evidence=evidence,
                            road_segment_id=v_seg_id,
                        )
                    )

        return risk_events


# -- module-level helpers ------------------------------------------------


def _index_segments(segments: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {s["segment_id"]: s for s in segments if "segment_id" in s}


def _group_vehicles_by_segment(
    vehicles: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for v in vehicles:
        seg = v.get("road_segment_id")
        if seg:
            out.setdefault(seg, []).append(v)
    return out


def _reachable_segments(
    start_seg_id: str,
    seg_index: dict[str, dict[str, Any]],
    max_distance_m: float,
) -> list[tuple[str, float, float]]:
    """BFS walk through connected segments, accumulating distance and curvature.

    Returns list of (segment_id, cumulative_distance_m, cumulative_curvature_deg)
    for every reachable segment within *max_distance_m* network distance.
    Only segments *other than* the start are included.
    """
    results: list[tuple[str, float, float]] = []
    visited: set[str] = {start_seg_id}
    start_seg = seg_index.get(start_seg_id)
    if start_seg is None:
        return results

    frontier: list[tuple[str, float, float]] = []
    start_len = start_seg.get("length_m", 0.0)
    start_curv = abs(start_seg.get("curvature_deg", 0.0))
    for neighbor_id in start_seg.get("connected_segments", []):
        if neighbor_id in seg_index:
            frontier.append((neighbor_id, start_len, start_curv))

    while frontier:
        seg_id, cum_dist, cum_curv = frontier.pop(0)
        if seg_id in visited:
            continue
        visited.add(seg_id)

        seg = seg_index.get(seg_id)
        if seg is None:
            continue

        seg_len = seg.get("length_m", 0.0)
        seg_curv = abs(seg.get("curvature_deg", 0.0))
        new_dist = cum_dist + seg_len
        new_curv = cum_curv + seg_curv

        if new_dist > max_distance_m:
            continue

        results.append((seg_id, new_dist, new_curv))

        for neighbor_id in seg.get("connected_segments", []):
            if neighbor_id not in visited:
                frontier.append((neighbor_id, new_dist, new_curv))

    return results


def _closing_speed(
    speed_a: float, speed_b: float, heading_a: float, heading_b: float,
) -> float:
    """Estimate closing speed between two actors.

    Opposing actors: speeds add.
    Same-direction actors: speed difference (faster closing on slower).
    Intermediate angles: cosine interpolation.
    """
    angle_diff = abs(bearing_difference(heading_a, heading_b))
    if angle_diff >= 180:
        angle_diff = 360 - angle_diff
    # At 180 deg (opposing): factor = 1 -> closing = speed_a + speed_b
    # At 0 deg (same dir):  factor = -1 -> closing = |speed_a - speed_b|
    import math
    factor = -math.cos(math.radians(angle_diff))
    if factor >= 0:
        return speed_a * factor + speed_b * factor + abs(speed_a - speed_b) * (1 - factor)
    # Mostly same direction
    return max(0.0, abs(speed_a - speed_b))


def _actor_network_distance(
    va: dict[str, Any],
    vb: dict[str, Any],
    path_distance: float,
    seg_index: dict[str, dict[str, Any]],
    seg_a_id: str,
    seg_b_id: str,
) -> float:
    """Approximate the network distance between two actors.

    Uses the cumulative path distance from BFS plus positional offsets
    within their respective segments (approximated via haversine to the
    segment endpoints / intersection).
    """
    # Best effort: path_distance is the cumulative segment-length distance
    # from the end of seg_a to the start of seg_b. We refine by subtracting
    # the portion of seg_a already traversed and adding the portion of seg_b
    # already traversed, but if we lack geometry we just use path_distance.
    pos_a = va.get("position", {})
    pos_b = vb.get("position", {})
    lat_a, lon_a = pos_a.get("lat"), pos_a.get("lon")
    lat_b, lon_b = pos_b.get("lat"), pos_b.get("lon")

    if lat_a is None or lon_a is None or lat_b is None or lon_b is None:
        return path_distance

    straight = haversine_distance(lat_a, lon_a, lat_b, lon_b)
    # Network distance is at least the straight-line distance, but use
    # the path walk estimate with a curvature factor.
    return max(straight, path_distance * 0.9)


def _visibility_limited(
    seg_a: dict[str, Any] | None,
    seg_b: dict[str, Any] | None,
    actor_distance_m: float,
) -> bool:
    """Determine if visibility is limited between two segments.

    Uses ``visibility_m`` metadata when available. In absence of metadata,
    returns True (conservative -- assume limited).
    """
    vis_a = (seg_a or {}).get("visibility_m")
    vis_b = (seg_b or {}).get("visibility_m")

    if vis_a is not None and vis_b is not None:
        effective_vis = min(vis_a, vis_b)
        return actor_distance_m > effective_vis

    if vis_a is not None:
        return actor_distance_m > vis_a
    if vis_b is not None:
        return actor_distance_m > vis_b

    # No metadata -- conservative: treat as limited.
    return True


def _compute_severity(
    closing_speed: float,
    network_distance_m: float,
    is_opposing: bool,
    visibility_limited: bool,
    threshold_distance_m: float,
) -> float:
    """Severity increases with closing speed and proximity, is higher for
    opposing trajectories, and higher when visibility is confirmed limited.
    """
    proximity_factor = max(0.0, 1.0 - (network_distance_m / threshold_distance_m))
    speed_factor = min(1.0, closing_speed / 30.0)  # 30 m/s ~ 108 km/h cap

    base = 0.3 * proximity_factor + 0.4 * speed_factor
    if is_opposing:
        base += 0.2
    if visibility_limited:
        base += 0.1

    return min(1.0, max(0.0, base))


def _compute_confidence(
    va: dict[str, Any],
    vb: dict[str, Any],
    visibility_limited: bool,
) -> float:
    """Confidence from position uncertainty and visibility information."""
    unc_a = va.get("position_uncertainty_m", 5.0)
    unc_b = vb.get("position_uncertainty_m", 5.0)
    # Lower uncertainty -> higher confidence.
    pos_conf = max(0.0, 1.0 - (unc_a + unc_b) / 40.0)
    # If we have confirmed visibility limitation, confidence is higher.
    vis_bonus = 0.1 if visibility_limited else 0.0
    return min(1.0, max(0.0, pos_conf + vis_bonus))
