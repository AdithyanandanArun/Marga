"""Canonical position estimation without adapter-specific dependencies."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from math import cos, exp, radians, sin

from packages.geo import LocalTangentPlane
from packages.schemas import GeoPoint, PositionEstimate, PositionMethod, VehicleState


@dataclass(frozen=True, slots=True)
class PositionPolicy:
    """Versioned, conservative configuration for position confidence."""

    max_dead_reckoning_s: float = 10.0
    dead_reckoning_drift_mps: float = 1.5
    confidence_decay_per_m: float = 0.04


class PositionService:
    """Produces a position estimate for a canonical state.

    This first slice intentionally has no sensor-vendor dependency.  It
    preserves a reported GNSS/SIM position and converts speed/heading into a
    local east/north vector.  ``dead_reckon`` is deterministic and provides a
    clear seam for later IMU/fusion inputs.
    """

    def __init__(self, policy: PositionPolicy | None = None) -> None:
        self.policy = policy or PositionPolicy()

    @staticmethod
    def _velocity(state: VehicleState) -> tuple[float, float]:
        heading_rad = radians(state.heading_deg)
        return state.speed_mps * sin(heading_rad), state.speed_mps * cos(heading_rad)

    def estimate(self, state: VehicleState) -> PositionEstimate:
        east, north = self._velocity(state)
        confidence = exp(-self.policy.confidence_decay_per_m * state.position_uncertainty_m)
        return PositionEstimate(
            actor_id=state.actor_id,
            ts=state.ts,
            position=state.position,
            velocity_east_mps=east,
            velocity_north_mps=north,
            uncertainty_radius_m=state.position_uncertainty_m,
            confidence=max(0.0, min(1.0, confidence)),
            method=PositionMethod.GNSS,
        )

    def dead_reckon(self, estimate: PositionEstimate, elapsed_s: float) -> PositionEstimate:
        """Advance an estimate in a local tangent plane, increasing uncertainty.

        Calls beyond the configured window are rejected rather than producing a
        deceptively precise estimate.
        """
        if not 0.0 <= elapsed_s <= self.policy.max_dead_reckoning_s:
            raise ValueError("elapsed_s must be within the configured dead-reckoning window")
        plane = LocalTangentPlane(estimate.position.lat, estimate.position.lon)
        lat, lon = plane.unproject(
            estimate.velocity_east_mps * elapsed_s,
            estimate.velocity_north_mps * elapsed_s,
        )
        uncertainty = (
            estimate.uncertainty_radius_m + elapsed_s * self.policy.dead_reckoning_drift_mps
        )
        confidence = estimate.confidence * exp(
            -self.policy.confidence_decay_per_m * elapsed_s * self.policy.dead_reckoning_drift_mps
        )
        return estimate.model_copy(
            update={
                "ts": estimate.ts + timedelta(seconds=elapsed_s),
                "position": GeoPoint(lat=lat, lon=lon, altitude_m=estimate.position.altitude_m),
                "uncertainty_radius_m": uncertainty,
                "confidence": max(0.0, min(1.0, confidence)),
                "method": PositionMethod.DEAD_RECKONED,
            }
        )
