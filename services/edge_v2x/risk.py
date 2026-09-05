"""Local edge risk evaluation for V2X safety conflicts.

Extends the canonical trajectory-based RiskEngine with edge-specific
conflict types required by the V2X safety story:
    - intersection conflict
    - head-on
    - rear-end
    - side-swipe
    - emergency braking
    - VRU (vulnerable road user) conflicts

All risk events use the canonical ``RiskEvent`` and ``RiskType`` from
``packages.schemas.canonical``.  No parallel risk types are created.

The evaluator operates on canonical ``VehicleState`` objects and produces
``RiskEvent`` objects with full evidence, confidence, and policy version.
"""

from __future__ import annotations

import logging
import math
from datetime import UTC, datetime, timedelta
from typing import Any

from packages.geo.coordinates import LocalTangentPlane, angular_difference_deg, distance_m
from packages.schemas.canonical import RiskEvent, RiskType, VehicleState
from marga_schemas.common import ActorType, GeoPoint

logger = logging.getLogger(__name__)

POLICY_VERSION = "edge-v2x-v1"

# Conflict classification thresholds (heading delta in degrees).
_HEAD_ON_THRESHOLD = 150.0
_REAR_END_THRESHOLD = 30.0
_SIDE_SWIPE_THRESHOLD = 60.0

# Emergency braking deceleration threshold (m/s^2).
_EMERGENCY_BRAKING_MPS2 = -4.0

# VRU types — these actors receive higher vulnerability weighting.
_VRU_TYPES = frozenset({
    ActorType.PEDESTRIAN,
    ActorType.BIKE,
    ActorType.ANIMAL,
})

# Vulnerability scores per actor type [0, 1].  Higher = more vulnerable.
_VULNERABILITY: dict[ActorType, float] = {
    ActorType.PEDESTRIAN: 1.0,
    ActorType.BIKE: 0.85,
    ActorType.ANIMAL: 0.70,
    ActorType.AUTO: 0.55,
    ActorType.CAR: 0.40,
    ActorType.BUS: 0.30,
    ActorType.TRUCK: 0.25,
    ActorType.AMBULANCE: 0.35,
    ActorType.OTHER: 0.40,
}


def actor_vulnerability(actor_type: ActorType | str) -> float:
    """Return vulnerability score [0, 1] for an actor type."""
    if isinstance(actor_type, str):
        try:
            actor_type = ActorType(actor_type)
        except ValueError:
            return 0.40
    return _VULNERABILITY.get(actor_type, 0.40)


def is_vru(actor_type: ActorType | str) -> bool:
    """Check if an actor type is a vulnerable road user."""
    if isinstance(actor_type, str):
        try:
            actor_type = ActorType(actor_type)
        except ValueError:
            return False
    return actor_type in _VRU_TYPES


class EdgeRiskEvaluator:
    """Local risk evaluator for edge V2X nodes.

    Evaluates pairwise conflicts between a node and its nearby peers.
    Covers intersection, head-on, rear-end, side-swipe, emergency-braking,
    and VRU conflicts.  All outputs carry confidence, evidence, and the
    policy version that produced them.

    This evaluator is map-independent: it works on arbitrary valid
    VehicleState inputs without hard-coded coordinates or actor IDs.
    """

    def __init__(
        self,
        *,
        horizon_s: float = 8.0,
        base_clearance_m: float = 2.5,
        max_eval_distance_m: float = 75.0,
    ) -> None:
        self.horizon_s = horizon_s
        self.base_clearance_m = base_clearance_m
        self.max_eval_distance_m = max_eval_distance_m

    def evaluate_pair(self, ego: VehicleState, peer: VehicleState) -> RiskEvent | None:
        """Evaluate a single ego-peer pair for all conflict types.

        Returns the highest-priority RiskEvent, or None if no conflict.
        """
        if ego.actor_id == peer.actor_id:
            return None

        # Distance check — skip distant pairs to avoid false positives.
        ego_pos = GeoPoint(lat=ego.position.lat, lon=ego.position.lon)
        peer_pos = GeoPoint(lat=peer.position.lat, lon=peer.position.lon)
        current_distance = distance_m(ego_pos, peer_pos)
        if current_distance > self.max_eval_distance_m:
            return None

        # Emergency braking detection: check if peer is braking hard.
        braking_event = self._check_emergency_braking(ego, peer, current_distance)
        if braking_event is not None:
            return braking_event

        # Trajectory-based conflict detection.
        return self._check_trajectory_conflict(ego, peer, current_distance)

    def evaluate_all(
        self, ego: VehicleState, peers: list[VehicleState]
    ) -> list[RiskEvent]:
        """Evaluate ego against all nearby peers.

        Returns all detected risks, not just the top one.  The prioritizer
        selects the single most important risk for the driver.
        """
        risks: list[RiskEvent] = []
        for peer in peers:
            risk = self.evaluate_pair(ego, peer)
            if risk is not None:
                risks.append(risk)
        return risks

    def _check_emergency_braking(
        self, ego: VehicleState, peer: VehicleState, current_distance: float
    ) -> RiskEvent | None:
        """Detect emergency braking by a peer ahead of the ego vehicle.

        Triggers when the peer's deceleration exceeds the threshold and
        the ego is behind (closing on) the peer.
        """
        peer_accel = peer.acceleration_mps2
        if peer_accel is None or peer_accel > _EMERGENCY_BRAKING_MPS2:
            return None

        # Check if ego is behind peer (same general direction, ego closing).
        heading_delta = abs(angular_difference_deg(ego.heading_deg, peer.heading_deg))
        if heading_delta > _REAR_END_THRESHOLD:
            return None  # Not same-direction, not a rear-end braking scenario.

        # Compute closing speed.
        closing_speed = ego.speed_mps - peer.speed_mps
        if closing_speed <= 0:
            return None  # Ego is not closing.

        ttc = current_distance / closing_speed if closing_speed > 0 else float("inf")
        if ttc > self.horizon_s:
            return None

        # Severity from deceleration magnitude and closing speed.
        decel_magnitude = abs(peer_accel)
        severity = min(1.0, 0.4 + 0.03 * decel_magnitude + 0.02 * closing_speed)
        confidence = min(1.0, 0.7 + 0.03 * decel_magnitude)
        vulnerability = max(
            actor_vulnerability(ego.actor_type),
            actor_vulnerability(peer.actor_type),
        )
        risk_score = severity * confidence * (0.5 + 0.5 * vulnerability)

        now = max(ego.ts, peer.ts).astimezone(UTC)
        return RiskEvent(
            type=RiskType.EMERGENCY_BRAKING,
            ts=now,
            affected_actor_ids=sorted([ego.actor_id, peer.actor_id]),
            time_to_conflict_s=round(ttc, 3),
            min_predicted_distance_m=round(current_distance, 3),
            severity=round(severity, 4),
            confidence=round(confidence, 4),
            risk_score=round(risk_score, 4),
            expires_at=now + timedelta(seconds=self.horizon_s),
            policy_version=POLICY_VERSION,
            evidence=[
                {
                    "type": "emergency_braking",
                    "peer_id": peer.actor_id,
                    "peer_deceleration_mps2": round(peer_accel, 3),
                    "closing_speed_mps": round(closing_speed, 3),
                    "distance_m": round(current_distance, 3),
                    "heading_delta_deg": round(heading_delta, 3),
                },
            ],
        )

    def _check_trajectory_conflict(
        self, ego: VehicleState, peer: VehicleState, current_distance: float
    ) -> RiskEvent | None:
        """Detect trajectory-based conflicts: head-on, rear-end, intersection, side-swipe.

        Uses constant-velocity projection in a local tangent plane, similar
        to the central RiskEngine but with additional side-swipe and VRU
        classification.
        """
        plane = LocalTangentPlane(ego.position.lat, ego.position.lon)
        east, north = plane.project(peer.position)

        # Constant-velocity projection.
        ego_heading_rad = math.radians(ego.heading_deg)
        peer_heading_rad = math.radians(peer.heading_deg)
        ego_vel = (
            ego.speed_mps * math.sin(ego_heading_rad),
            ego.speed_mps * math.cos(ego_heading_rad),
        )
        peer_vel = (
            peer.speed_mps * math.sin(peer_heading_rad),
            peer.speed_mps * math.cos(peer_heading_rad),
        )
        rel_vel = (peer_vel[0] - ego_vel[0], peer_vel[1] - ego_vel[1])
        vel_sq = rel_vel[0] ** 2 + rel_vel[1] ** 2

        if vel_sq <= 0.01:
            return None  # Negligible relative motion.

        ttc = max(
            0.0,
            min(
                self.horizon_s,
                -(east * rel_vel[0] + north * rel_vel[1]) / vel_sq,
            ),
        )

        closest_east = east + rel_vel[0] * ttc
        closest_north = north + rel_vel[1] * ttc
        min_distance = math.sqrt(closest_east**2 + closest_north**2)

        combined_uncertainty = ego.position_uncertainty_m + peer.position_uncertainty_m
        clearance = self.base_clearance_m + combined_uncertainty

        if min_distance > clearance or ttc >= self.horizon_s:
            return None

        # Classify conflict type by heading delta.
        heading_delta = abs(angular_difference_deg(ego.heading_deg, peer.heading_deg))
        risk_type = self._classify_conflict(heading_delta, ego, peer, min_distance)
        conflict_pattern = risk_type.value.lower()
        if risk_type == RiskType.COLLISION and heading_delta <= _SIDE_SWIPE_THRESHOLD:
            conflict_pattern = "side_swipe"

        # Same-road same-direction filter: avoid false positives for
        # vehicles travelling in parallel lanes.
        if (
            ego.road_segment_id
            and ego.road_segment_id == peer.road_segment_id
            and heading_delta <= _REAR_END_THRESHOLD
            and current_distance > 18.0
        ):
            return None

        # Compute severity, confidence, and vulnerability.
        closeness = max(0.0, min(1.0, 1.0 - min_distance / clearance))
        imminence = 1.0 - ttc / self.horizon_s
        severity = max(0.0, min(1.0, 0.55 * closeness + 0.45 * imminence))

        # VRU boost: conflicts involving vulnerable road users are more severe.
        max_vulnerability = max(
            actor_vulnerability(ego.actor_type),
            actor_vulnerability(peer.actor_type),
        )
        if is_vru(ego.actor_type) or is_vru(peer.actor_type):
            severity = min(1.0, severity * (0.8 + 0.4 * max_vulnerability))

        confidence = max(
            0.0,
            min(1.0, math.exp(-combined_uncertainty / 40.0)),
        )
        risk_score = severity * confidence * (0.5 + 0.5 * max_vulnerability)

        now = max(ego.ts, peer.ts).astimezone(UTC)
        return RiskEvent(
            type=risk_type,
            ts=now,
            affected_actor_ids=sorted([ego.actor_id, peer.actor_id]),
            time_to_conflict_s=round(ttc, 3),
            min_predicted_distance_m=round(min_distance, 3),
            severity=round(severity, 4),
            confidence=round(confidence, 4),
            risk_score=round(risk_score, 4),
            expires_at=now + timedelta(seconds=self.horizon_s),
            policy_version=POLICY_VERSION,
            evidence=[
                {
                    "type": "trajectory_ttc",
                    "model": "constant_velocity_local_tangent_plane",
                    "conflict_pattern": conflict_pattern,
                    "horizon_s": self.horizon_s,
                    "combined_uncertainty_m": round(combined_uncertainty, 3),
                    "clearance_m": round(clearance, 3),
                    "heading_delta_deg": round(heading_delta, 3),
                    "ego_id": ego.actor_id,
                    "peer_id": peer.actor_id,
                    "ego_vru": is_vru(ego.actor_type),
                    "peer_vru": is_vru(peer.actor_type),
                    "max_vulnerability": round(max_vulnerability, 4),
                },
            ],
        )

    def _classify_conflict(
        self,
        heading_delta: float,
        ego: VehicleState,
        peer: VehicleState,
        min_distance: float,
    ) -> RiskType:
        """Classify the conflict type from heading delta and road context.

        - >= 150 deg: head-on
        - <= 30 deg: rear-end (or side-swipe if lateral separation is small)
        - 30-60 deg: intersection conflict
        - 60-150 deg: intersection conflict
        """
        if heading_delta >= _HEAD_ON_THRESHOLD:
            return RiskType.HEAD_ON

        if heading_delta <= _REAR_END_THRESHOLD:
            # Side-swipe: same general direction but lateral overlap.
            # Distinguish from rear-end by checking if the minimum distance
            # is very small relative to the current distance (lateral merge).
            if min_distance < self.base_clearance_m and heading_delta > 10:
                # Side-swipe is classified as COLLISION with evidence
                # indicating the side-swipe pattern.  The canonical RiskType
                # enum does not have a dedicated SIDE_SWIPE value, so we
                # use COLLISION to avoid creating parallel risk types.
                return RiskType.COLLISION
            return RiskType.REAR_END

        # Check for VRU-specific conflict type.
        if is_vru(ego.actor_type) or is_vru(peer.actor_type):
            return RiskType.PEDESTRIAN_CONFLICT

        return RiskType.INTERSECTION_CONFLICT
