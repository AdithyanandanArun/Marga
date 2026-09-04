from datetime import UTC, datetime

import pytest

from packages.schemas import ActorType, GeoPoint, Source, VehicleState
from services.position.service import PositionService
from services.risk import RiskEngine


def vehicle(
    actor_id: str, *, lat: float, lon: float, heading: float, speed: float, uncertainty: float = 1.0
) -> VehicleState:
    return VehicleState(
        actor_id=actor_id,
        actor_type=ActorType.CAR,
        ts=datetime(2026, 9, 4, tzinfo=UTC),
        position=GeoPoint(lat=lat, lon=lon),
        position_uncertainty_m=uncertainty,
        speed_mps=speed,
        heading_deg=heading,
        source=Source.SIMULATION,
    )


def test_position_estimate_and_dead_reckoning_preserve_uncertainty() -> None:
    service = PositionService()
    estimate = service.estimate(
        vehicle("northbound", lat=12.9716, lon=77.5946, heading=0, speed=10)
    )
    advanced = service.dead_reckon(estimate, 2)
    assert estimate.velocity_north_mps == pytest.approx(10)
    assert estimate.velocity_east_mps == pytest.approx(0)
    assert advanced.position.lat > estimate.position.lat
    assert advanced.uncertainty_radius_m > estimate.uncertainty_radius_m
    assert advanced.confidence < estimate.confidence


def test_generic_risk_detects_converging_actors_with_evidence() -> None:
    positions = PositionService()
    first = positions.estimate(vehicle("a", lat=12.9716, lon=77.5946, heading=90, speed=10))
    second = positions.estimate(vehicle("b", lat=12.9716, lon=77.5948, heading=270, speed=10))
    risk = RiskEngine().evaluate_pair(first, second, detected_at=datetime(2026, 9, 4, tzinfo=UTC))
    assert risk is not None
    assert risk.time_to_conflict_s is not None and risk.time_to_conflict_s < 8
    assert risk.evidence["model"] == "constant_velocity_local_tangent_plane"
    assert risk.confidence < 1


def test_generic_risk_ignores_non_conflicting_actors() -> None:
    positions = PositionService()
    first = positions.estimate(vehicle("a", lat=12.9716, lon=77.5946, heading=0, speed=10))
    second = positions.estimate(vehicle("b", lat=12.9800, lon=77.5946, heading=0, speed=10))
    assert RiskEngine().evaluate_pair(first, second) is None
