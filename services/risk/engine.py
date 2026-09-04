"""Deterministic, uncertainty-aware generic collision-risk evaluation."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from math import exp

from packages.geo import LocalTangentPlane
from packages.schemas import PositionEstimate, RiskEvent, RiskType


@dataclass(frozen=True, slots=True)
class RiskPolicy:
    """Versioned thresholds, deliberately independent of simulator/map IDs."""

    version: str = "v1"
    horizon_s: float = 8.0
    base_clearance_m: float = 2.5
    min_relative_speed_mps: float = 0.1

    def __post_init__(self) -> None:
        if self.horizon_s <= 0 or self.base_clearance_m < 0 or self.min_relative_speed_mps < 0:
            raise ValueError("risk policy thresholds must be non-negative and horizon positive")


class RiskEngine:
    """Evaluates pairwise constant-velocity conflicts in a local metric frame.

    This is a generic baseline, not a map-specific detector.  It returns
    `None` for pairs that cannot conflict inside the configured horizon.
    """

    def __init__(self, policy: RiskPolicy | None = None) -> None:
        self.policy = policy or RiskPolicy()

    @staticmethod
    def _dot(a: tuple[float, float], b: tuple[float, float]) -> float:
        return a[0] * b[0] + a[1] * b[1]

    def evaluate_pair(
        self,
        first: PositionEstimate,
        second: PositionEstimate,
        *,
        detected_at: datetime | None = None,
    ) -> RiskEvent | None:
        if first.actor_id == second.actor_id:
            return None
        detected_at = (detected_at or datetime.now(UTC)).astimezone(UTC)
        plane = LocalTangentPlane(first.position.lat, first.position.lon)
        second_east, second_north = plane.project(second.position)
        relative_position = (second_east, second_north)
        relative_velocity = (
            second.velocity_east_mps - first.velocity_east_mps,
            second.velocity_north_mps - first.velocity_north_mps,
        )
        velocity_sq = self._dot(relative_velocity, relative_velocity)
        if velocity_sq <= self.policy.min_relative_speed_mps**2:
            time_to_closest = 0.0
        else:
            time_to_closest = max(
                0.0,
                min(
                    self.policy.horizon_s,
                    -self._dot(relative_position, relative_velocity) / velocity_sq,
                ),
            )
        closest = (
            relative_position[0] + relative_velocity[0] * time_to_closest,
            relative_position[1] + relative_velocity[1] * time_to_closest,
        )
        separation = self._dot(closest, closest) ** 0.5
        combined_uncertainty = first.uncertainty_radius_m + second.uncertainty_radius_m
        conflict_distance = self.policy.base_clearance_m + combined_uncertainty
        if separation > conflict_distance:
            return None
        closeness = (
            1.0
            if conflict_distance == 0
            else max(0.0, min(1.0, 1.0 - separation / conflict_distance))
        )
        imminence = 1.0 - time_to_closest / self.policy.horizon_s
        severity = max(0.0, min(1.0, 0.55 * closeness + 0.45 * imminence))
        confidence = max(
            0.0, min(1.0, first.confidence * second.confidence * exp(-combined_uncertainty / 40.0))
        )
        return RiskEvent(
            type=RiskType.GENERIC_CONFLICT,
            detected_at=detected_at,
            actor_ids=tuple(sorted((first.actor_id, second.actor_id))),
            severity=severity,
            confidence=confidence,
            time_to_conflict_s=time_to_closest,
            minimum_separation_m=separation,
            policy_version=self.policy.version,
            evidence={
                "model": "constant_velocity_local_tangent_plane",
                "horizon_s": self.policy.horizon_s,
                "base_clearance_m": self.policy.base_clearance_m,
                "combined_uncertainty_m": combined_uncertainty,
                "conflict_distance_m": conflict_distance,
                "relative_velocity_mps": {
                    "east": relative_velocity[0],
                    "north": relative_velocity[1],
                },
                "source_estimate_ids": [str(first.estimate_id), str(second.estimate_id)],
            },
        )

    def evaluate_all(
        self, estimates: Iterable[PositionEstimate], *, detected_at: datetime | None = None
    ) -> list[RiskEvent]:
        """Evaluate all unique pairs; callers can replace this with a spatial index."""
        ordered = list(estimates)
        risks: list[RiskEvent] = []
        for index, first in enumerate(ordered):
            for second in ordered[index + 1 :]:
                risk = self.evaluate_pair(first, second, detected_at=detected_at)
                if risk is not None:
                    risks.append(risk)
        return risks
