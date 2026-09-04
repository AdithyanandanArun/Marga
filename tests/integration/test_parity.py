"""
AM.4 — Simulation-reality parity test.

Verifies that a mock_sim adapter envelope and a real-adapter-shaped canonical
VehicleState produce equivalent canonical state when processed through the
same canonical bridge.  Covers all actor types Amrita's scenario uses.
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from packages.schemas.canonical import ActorType, Position, SourceType, VehicleState
from services.integration.canonical_bridge import vehicle_from_adapter_event


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_sim_event(
    actor_id: str,
    vehicle_type: str,
    lat: float,
    lon: float,
    speed_mps: float,
    heading_deg: float,
    uncertainty_m: float = 4.0,
) -> dict:
    """Produce an actor.state.updated envelope as mock_sim would emit."""
    ts = datetime.now(UTC).isoformat()
    return {
        "event_type": "actor.state.updated",
        "timestamp_utc": ts,
        "source": "mock_sim",
        "payload": {
            "vehicle_id": actor_id,
            "actor_id": actor_id,
            "ts": ts,
            "actor_type": vehicle_type,
            "vehicle_type": vehicle_type,
            "position": {"lat": lat, "lon": lon, "uncertainty_m": uncertainty_m},
            "position_uncertainty_m": uncertainty_m,
            "speed_mps": speed_mps,
            "heading_deg": heading_deg,
            "source": "SIMULATION",
        },
    }


def _real_adapter_state(
    actor_id: str,
    actor_type: ActorType,
    lat: float,
    lon: float,
    speed_mps: float,
    heading_deg: float,
    uncertainty_m: float = 4.0,
) -> VehicleState:
    """Produce a VehicleState as a real hardware adapter (OBU/GNSS) would emit."""
    return VehicleState(
        actor_id=actor_id,
        actor_type=actor_type,
        ts=datetime.now(UTC),
        position=Position(lat=lat, lon=lon),
        position_uncertainty_m=uncertainty_m,
        speed_mps=speed_mps,
        heading_deg=heading_deg,
        source=SourceType.OBU,
    )


# ---------------------------------------------------------------------------
# Parity assertions
# ---------------------------------------------------------------------------

def _assert_parity(sim: VehicleState, real: VehicleState) -> None:
    """Key fields must match; source/schema_version are allowed to differ."""
    assert sim.actor_id == real.actor_id, "actor_id mismatch"
    assert sim.actor_type == real.actor_type, "actor_type mismatch"
    assert abs(sim.position.lat - real.position.lat) < 1e-6, "lat mismatch"
    assert abs(sim.position.lon - real.position.lon) < 1e-6, "lon mismatch"
    assert abs(sim.speed_mps - real.speed_mps) < 1e-3, "speed_mps mismatch"
    assert abs(sim.heading_deg - real.heading_deg) < 1e-2, "heading_deg mismatch"
    assert abs(sim.position_uncertainty_m - real.position_uncertainty_m) < 1e-3, "uncertainty mismatch"


# ---------------------------------------------------------------------------
# Test cases — one per actor type that Amrita's scenario uses
# ---------------------------------------------------------------------------

# Junction center: Shivajinagar, Bangalore
_LAT = 12.9822
_LON = 77.5935


@pytest.mark.parametrize("vehicle_type,actor_type,heading,speed", [
    ("auto_rickshaw", ActorType.AUTO,  90.0, 5.5),   # ego_auto heading east
    ("bus",           ActorType.BUS,    0.0, 6.5),   # conflict_bus heading north
    ("car",           ActorType.CAR,  180.0, 8.0),   # bg_car_1 heading south
    ("motorcycle",    ActorType.BIKE,  270.0, 9.5),  # bg_moto heading west
])
def test_sim_real_parity(vehicle_type: str, actor_type: ActorType, heading: float, speed: float) -> None:
    actor_id = f"parity-{vehicle_type}"
    lat, lon = _LAT, _LON

    sim_event = _mock_sim_event(actor_id, vehicle_type, lat, lon, speed, heading)
    sim_vs = vehicle_from_adapter_event(sim_event)
    real_vs = _real_adapter_state(actor_id, actor_type, lat, lon, speed, heading)

    _assert_parity(sim_vs, real_vs)


def test_gps_degraded_uncertainty_preserved() -> None:
    """Position uncertainty from a degraded-GPS sim event must survive normalization."""
    event = _mock_sim_event(
        "ego_auto", "auto_rickshaw", _LAT, _LON,
        speed_mps=0.0, heading_deg=90.0,
        uncertainty_m=25.0,  # degraded GPS phase
    )
    vs = vehicle_from_adapter_event(event)
    assert vs.position_uncertainty_m == pytest.approx(25.0)


def test_heading_normalization() -> None:
    """360° heading must normalize to 0°."""
    event = _mock_sim_event("bg_car_1", "car", _LAT, _LON, speed_mps=8.0, heading_deg=360.0)
    vs = vehicle_from_adapter_event(event)
    assert vs.heading_deg == pytest.approx(0.0)


def test_junction_all_actors_parity() -> None:
    """All 5 junction actors produced by mock_sim must normalize to valid VehicleState."""
    scenario_actors = [
        ("ego_auto",      "auto_rickshaw", ActorType.AUTO,  90.0,  5.5),
        ("conflict_bus",  "bus",           ActorType.BUS,    0.0,  6.5),
        ("bg_car_1",      "car",           ActorType.CAR,  180.0,  8.0),
        ("bg_car_2",      "car",           ActorType.CAR,  270.0,  7.5),
        ("bg_moto",       "motorcycle",    ActorType.BIKE,  90.0,  9.5),
    ]
    for actor_id, v_type, a_type, hdg, spd in scenario_actors:
        event = _mock_sim_event(actor_id, v_type, _LAT, _LON, spd, hdg)
        sim_vs = vehicle_from_adapter_event(event)
        real_vs = _real_adapter_state(actor_id, a_type, _LAT, _LON, spd, hdg)
        _assert_parity(sim_vs, real_vs)
