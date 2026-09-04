"""Shared pytest fixtures for safety acceptance tests.

Provides factory functions and reusable scenario fixtures for all
safety detector and evaluation tests.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from packages.geo.helpers import point_along_bearing
from packages.safety_policies.config import PolicyConfig
from packages.schemas.canonical import (
    ActorType,
    DynamicActorObservation,
    Hazard,
    HazardState,
    HazardType,
    Position,
    RiskEvent,
    RiskType,
    SourceType,
    VehicleState,
)


# ---------------------------------------------------------------------------
# Factory functions
# ---------------------------------------------------------------------------

def make_vehicle_state(
    *,
    actor_id: str | None = None,
    actor_type: ActorType = ActorType.CAR,
    lat: float = 12.9716,
    lon: float = 77.5946,
    speed_mps: float = 10.0,
    heading_deg: float = 0.0,
    acceleration_mps2: float | None = None,
    road_segment_id: str | None = "seg-1",
    lane_id: str | None = "lane-1",
    position_uncertainty_m: float = 2.0,
    ts: datetime | None = None,
    source: SourceType = SourceType.SIMULATION,
) -> VehicleState:
    """Create a VehicleState with sensible defaults."""
    return VehicleState(
        actor_id=actor_id or str(uuid.uuid4()),
        actor_type=actor_type,
        ts=ts or datetime.now(timezone.utc),
        position=Position(lat=lat, lon=lon),
        position_uncertainty_m=position_uncertainty_m,
        speed_mps=speed_mps,
        acceleration_mps2=acceleration_mps2,
        heading_deg=heading_deg,
        road_segment_id=road_segment_id,
        lane_id=lane_id,
        source=source,
    )


def vehicle_to_dict(vs: VehicleState) -> dict[str, Any]:
    """Convert a VehicleState to a plain dict suitable for detectors
    that expect dict-format vehicles (blind_intersection, animal_conflict)."""
    return vs.model_dump(mode="python")


def make_hazard(
    *,
    hazard_type: HazardType = HazardType.POTHOLE,
    lat: float = 12.9716,
    lon: float = 77.5946,
    severity: float = 0.5,
    confidence: float = 0.8,
    state: HazardState = HazardState.CANDIDATE,
    ttl_s: int = 3600,
    source_ids: list[str] | None = None,
    first_seen: datetime | None = None,
    last_seen: datetime | None = None,
) -> Hazard:
    """Create a Hazard with sensible defaults."""
    now = datetime.now(timezone.utc)
    return Hazard(
        type=hazard_type,
        geometry={"type": "Point", "coordinates": [lon, lat]},
        severity=severity,
        confidence=confidence,
        first_seen=first_seen or now,
        last_seen=last_seen or now,
        ttl_s=ttl_s,
        source_ids=source_ids or [str(uuid.uuid4())],
        evidence_count=1,
        state=state,
    )


def make_risk_event(
    *,
    risk_type: RiskType = RiskType.COLLISION,
    affected_actor_ids: list[str] | None = None,
    severity: float = 0.7,
    confidence: float = 0.8,
    risk_score: float | None = None,
    evidence: list[dict[str, Any]] | None = None,
    ts: datetime | None = None,
    expires_at: datetime | None = None,
    road_segment_id: str | None = None,
    time_to_conflict_s: float | None = None,
) -> RiskEvent:
    """Create a RiskEvent with sensible defaults."""
    actor_ids = affected_actor_ids or [str(uuid.uuid4())]
    score = risk_score if risk_score is not None else severity * confidence
    return RiskEvent(
        type=risk_type,
        ts=ts or datetime.now(timezone.utc),
        affected_actor_ids=actor_ids,
        severity=severity,
        confidence=confidence,
        risk_score=score,
        evidence=evidence or [{"type": "test", "detail": "fixture-generated"}],
        expires_at=expires_at,
        road_segment_id=road_segment_id,
        time_to_conflict_s=time_to_conflict_s,
    )


def make_observation(
    *,
    actor_class: str = "cow",
    lat: float = 12.9716,
    lon: float = 77.5946,
    speed_mps: float | None = 2.0,
    heading_deg: float | None = 90.0,
    detector_confidence: float = 0.8,
    source_id: str | None = None,
    track_id: str | None = None,
    ts: datetime | None = None,
) -> DynamicActorObservation:
    """Create a DynamicActorObservation with sensible defaults."""
    return DynamicActorObservation(
        actor_class=actor_class,
        ts=ts or datetime.now(timezone.utc),
        position=Position(lat=lat, lon=lon),
        position_uncertainty_m=5.0,
        speed_mps=speed_mps,
        heading_deg=heading_deg,
        detector_confidence=detector_confidence,
        source_id=source_id or str(uuid.uuid4()),
        track_id=track_id,
    )


def make_segment(
    segment_id: str,
    direction_deg: float = 0.0,
    road_type: str = "URBAN",
    connected_segments: list[str] | None = None,
) -> dict[str, Any]:
    """Create a road segment dict in the format expected by detectors."""
    return {
        "segment_id": segment_id,
        "direction_deg": direction_deg,
        "type": road_type,
        "connected_segments": connected_segments or [],
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def policy_config() -> PolicyConfig:
    """Default PolicyConfig for testing."""
    return PolicyConfig()


@pytest.fixture()
def road_network() -> dict[str, Any]:
    """Simple road network with two segments and one intersection."""
    return {
        "segments": [
            make_segment("seg-north", direction_deg=0.0, road_type="URBAN"),
            make_segment("seg-east", direction_deg=90.0, road_type="URBAN"),
            make_segment("seg-highway", direction_deg=45.0, road_type="HIGHWAY"),
        ],
    }


@pytest.fixture()
def straight_road_scenario(road_network: dict[str, Any]) -> dict[str, Any]:
    """Two vehicles approaching each other on a straight north-bound road."""
    now = datetime.now(timezone.utc)
    v1_lat, v1_lon = point_along_bearing(12.9716, 77.5946, 180.0, 200.0)
    v2_lat, v2_lon = point_along_bearing(12.9716, 77.5946, 0.0, 200.0)

    v1 = make_vehicle_state(
        lat=v1_lat, lon=v1_lon, heading_deg=0.0, speed_mps=15.0,
        road_segment_id="seg-north", ts=now,
    )
    v2 = make_vehicle_state(
        lat=v2_lat, lon=v2_lon, heading_deg=180.0, speed_mps=15.0,
        road_segment_id="seg-north", ts=now,
    )
    return {
        "vehicles": [v1, v2],
        "road_network": road_network,
        "hazards": [],
        "observations": [],
        "signals": {},
    }


@pytest.fixture()
def intersection_scenario(road_network: dict[str, Any]) -> dict[str, Any]:
    """Two vehicles approaching the same intersection from perpendicular roads."""
    now = datetime.now(timezone.utc)
    int_lat, int_lon = 12.9716, 77.5946

    v1_lat, v1_lon = point_along_bearing(int_lat, int_lon, 180.0, 50.0)
    v2_lat, v2_lon = point_along_bearing(int_lat, int_lon, 270.0, 50.0)

    v1 = make_vehicle_state(
        lat=v1_lat, lon=v1_lon, heading_deg=0.0, speed_mps=10.0,
        road_segment_id="seg-north", ts=now,
    )
    v2 = make_vehicle_state(
        lat=v2_lat, lon=v2_lon, heading_deg=90.0, speed_mps=10.0,
        road_segment_id="seg-east", ts=now,
    )
    return {
        "vehicles": [v1, v2],
        "road_network": road_network,
        "hazards": [],
        "observations": [],
        "signals": {},
    }
