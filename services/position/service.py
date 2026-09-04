"""Canonical, uncertainty-aware position fusion and trajectory prediction.

The module intentionally consumes only ``packages.schemas.canonical`` models.
Adapters remain responsible for translating GNSS, RSU, OBU, or simulator data
before it reaches this service.
"""

from __future__ import annotations

from datetime import timedelta
from math import exp

from pydantic import BaseModel, Field

from packages.geo import project_position
from packages.schemas.canonical import Position, VehicleState


class TrajectoryPoint(BaseModel):
    """A conservative constant-acceleration trajectory sample."""

    offset_s: float = Field(ge=0)
    position: Position
    speed_mps: float = Field(ge=0)
    uncertainty_m: float = Field(ge=0)


class TrajectoryPrediction(BaseModel):
    """Near-term trajectory plus the uncertainty used by collision evaluation."""

    actor_id: str
    source_ts: str
    points: list[TrajectoryPoint]
    confidence: float = Field(ge=0, le=1)
    method: str = "constant_acceleration"


def predict_trajectory(
    state: VehicleState,
    *,
    horizon_s: float = 8.0,
    interval_s: float = 1.0,
    uncertainty_growth_mps: float = 1.5,
) -> TrajectoryPrediction:
    """Project a canonical vehicle state without manufacturing precision.

    Uncertainty grows with prediction age.  This makes degraded positioning
    reduce the confidence of every risk computed from the trajectory.
    """
    if horizon_s <= 0 or interval_s <= 0 or uncertainty_growth_mps < 0:
        raise ValueError("trajectory horizon, interval, and uncertainty growth must be positive")
    acceleration = state.acceleration_mps2 or 0.0
    points: list[TrajectoryPoint] = []
    offset = 0.0
    while offset <= horizon_s + 1e-9:
        lat, lon, speed = project_position(
            state.position.lat,
            state.position.lon,
            state.heading_deg,
            state.speed_mps,
            offset,
            acceleration,
        )
        points.append(
            TrajectoryPoint(
                offset_s=round(offset, 6),
                position=Position(lat=lat, lon=lon, altitude_m=state.position.altitude_m),
                speed_mps=speed,
                uncertainty_m=state.position_uncertainty_m + offset * uncertainty_growth_mps,
            )
        )
        offset += interval_s
    if points[-1].offset_s < horizon_s:
        lat, lon, speed = project_position(
            state.position.lat, state.position.lon, state.heading_deg, state.speed_mps, horizon_s, acceleration
        )
        points.append(
            TrajectoryPoint(
                offset_s=horizon_s,
                position=Position(lat=lat, lon=lon, altitude_m=state.position.altitude_m),
                speed_mps=speed,
                uncertainty_m=state.position_uncertainty_m + horizon_s * uncertainty_growth_mps,
            )
        )
    confidence = exp(-points[-1].uncertainty_m / 40.0)
    return TrajectoryPrediction(
        actor_id=state.actor_id,
        source_ts=state.ts.isoformat(),
        points=points,
        confidence=max(0.0, min(1.0, confidence)),
    )


class PositionFusionService:
    """Fuse independent canonical observations using inverse-variance weighting."""

    min_uncertainty_m = 0.1

    def fuse(self, observations: list[VehicleState]) -> VehicleState:
        """Return a fused state for observations of the same actor.

        Sources with a smaller reported uncertainty receive greater weight. The
        newest kinematic fields are retained, while the position and its
        uncertainty are fused.  Mixing actor IDs is rejected to avoid silent
        cross-actor contamination.
        """
        if not observations:
            raise ValueError("at least one observation is required")
        actor_ids = {item.actor_id for item in observations}
        if len(actor_ids) != 1:
            raise ValueError("position observations must belong to one actor")
        latest = max(observations, key=lambda item: item.ts)
        lat_weighted = lon_weighted = total_weight = 0.0
        for item in observations:
            variance = max(item.position_uncertainty_m, self.min_uncertainty_m) ** 2
            weight = 1.0 / variance
            lat_weighted += item.position.lat * weight
            lon_weighted += item.position.lon * weight
            total_weight += weight
        uncertainty = (1.0 / total_weight) ** 0.5
        capabilities = list(dict.fromkeys([*latest.capabilities, "position-fused"]))
        return latest.model_copy(
            update={
                "position": Position(
                    lat=lat_weighted / total_weight,
                    lon=lon_weighted / total_weight,
                    altitude_m=latest.position.altitude_m,
                ),
                "position_uncertainty_m": uncertainty,
                "capabilities": capabilities,
            }
        )

    def fuse_with_previous(self, previous: VehicleState | None, observed: VehicleState) -> VehicleState:
        """Fuse a current observation with a recent prior for the same actor."""
        if previous is None or previous.actor_id != observed.actor_id:
            return observed
        elapsed = (observed.ts - previous.ts).total_seconds()
        if elapsed <= 0 or elapsed > 10.0:
            return observed
        # Advance the prior uncertainty before treating it as a second source.
        predicted = previous.model_copy(
            update={
                "position_uncertainty_m": previous.position_uncertainty_m + elapsed * 1.5,
                "ts": previous.ts + timedelta(seconds=elapsed),
            }
        )
        return self.fuse([predicted, observed])
