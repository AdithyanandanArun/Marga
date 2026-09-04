"""Constant-velocity collision risk evaluation over canonical trajectories."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, timedelta
from math import exp

from packages.geo import LocalTangentPlane, angular_difference_deg
from packages.schemas.canonical import RiskEvent, RiskType, VehicleState
from services.position import predict_trajectory


@dataclass(frozen=True, slots=True)
class RiskPolicy:
    """Versioned, map-independent safety thresholds."""

    version: str = "trajectory-ttc-v1"
    horizon_s: float = 8.0
    base_clearance_m: float = 2.5
    minimum_relative_speed_mps: float = 0.1


class RiskEngine:
    """Find pairwise closest approaches in a local metric coordinate frame."""

    def __init__(self, policy: RiskPolicy | None = None) -> None:
        self.policy = policy or RiskPolicy()

    def evaluate_pair(self, first: VehicleState, second: VehicleState) -> RiskEvent | None:
        if first.actor_id == second.actor_id:
            return None
        plane = LocalTangentPlane(first.position.lat, first.position.lon)
        east, north = plane.project(second.position)
        import math

        first_heading = math.radians(first.heading_deg)
        second_heading = math.radians(second.heading_deg)
        first_velocity = (first.speed_mps * math.sin(first_heading), first.speed_mps * math.cos(first_heading))
        second_velocity = (second.speed_mps * math.sin(second_heading), second.speed_mps * math.cos(second_heading))
        relative_velocity = (second_velocity[0] - first_velocity[0], second_velocity[1] - first_velocity[1])
        velocity_sq = relative_velocity[0] ** 2 + relative_velocity[1] ** 2
        if velocity_sq <= self.policy.minimum_relative_speed_mps**2:
            return None
        time_to_conflict = max(
            0.0,
            min(
                self.policy.horizon_s,
                -(east * relative_velocity[0] + north * relative_velocity[1]) / velocity_sq,
            ),
        )
        closest_east = east + relative_velocity[0] * time_to_conflict
        closest_north = north + relative_velocity[1] * time_to_conflict
        min_distance = (closest_east**2 + closest_north**2) ** 0.5
        predicted_first = predict_trajectory(first, horizon_s=self.policy.horizon_s)
        predicted_second = predict_trajectory(second, horizon_s=self.policy.horizon_s)
        combined_uncertainty = first.position_uncertainty_m + second.position_uncertainty_m
        clearance = self.policy.base_clearance_m + combined_uncertainty
        if min_distance > clearance or time_to_conflict >= self.policy.horizon_s:
            return None
        heading_delta = abs(angular_difference_deg(first.heading_deg, second.heading_deg))
        if heading_delta >= 150:
            risk_type = RiskType.HEAD_ON
        elif heading_delta <= 30:
            risk_type = RiskType.REAR_END
        else:
            risk_type = RiskType.INTERSECTION_CONFLICT
        closeness = max(0.0, min(1.0, 1.0 - min_distance / clearance))
        imminence = 1.0 - time_to_conflict / self.policy.horizon_s
        severity = max(0.0, min(1.0, 0.55 * closeness + 0.45 * imminence))
        confidence = max(
            0.0,
            min(
                1.0,
                predicted_first.confidence * predicted_second.confidence * exp(-combined_uncertainty / 40.0),
            ),
        )
        detected_at = max(first.ts, second.ts).astimezone(UTC)
        return RiskEvent(
            type=risk_type,
            ts=detected_at,
            affected_actor_ids=sorted([first.actor_id, second.actor_id]),
            time_to_conflict_s=round(time_to_conflict, 3),
            min_predicted_distance_m=round(min_distance, 3),
            severity=round(severity, 4),
            confidence=round(confidence, 4),
            risk_score=round(severity * confidence, 4),
            expires_at=detected_at + timedelta(seconds=self.policy.horizon_s),
            policy_version=self.policy.version,
            evidence=[
                {
                    "type": "trajectory_ttc",
                    "model": "constant_velocity_local_tangent_plane",
                    "horizon_s": self.policy.horizon_s,
                    "combined_uncertainty_m": round(combined_uncertainty, 3),
                    "clearance_m": round(clearance, 3),
                    "heading_delta_deg": round(heading_delta, 3),
                    "actor_id": first.actor_id,
                },
                {
                    "type": "trajectory",
                    "actor_id": second.actor_id,
                    "minimum_distance_m": round(min_distance, 3),
                },
            ],
        )

    def evaluate_all(self, states: list[VehicleState]) -> list[RiskEvent]:
        risks: list[RiskEvent] = []
        for index, first in enumerate(states):
            for second in states[index + 1 :]:
                risk = self.evaluate_pair(first, second)
                if risk is not None:
                    risks.append(risk)
        return risks
