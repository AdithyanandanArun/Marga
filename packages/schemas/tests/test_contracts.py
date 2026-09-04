from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from pydantic import ValidationError

from packages.schemas import (
    Alert,
    AlertPriority,
    EventEnvelope,
    EventType,
    GeoJSONPoint,
    Hazard,
    HazardType,
    VehicleState,
)

NOW = datetime(2026, 9, 4, 10, 0, tzinfo=UTC)


def vehicle_payload() -> dict[str, object]:
    return {
        "actor_id": "sumo:car-1",
        "actor_type": "CAR",
        "ts": NOW.isoformat(),
        "position": {"lat": 12.9716, "lon": 77.5946},
        "position_uncertainty_m": 3.0,
        "speed_mps": 12.5,
        "heading_deg": 360,
        "source": "SIMULATION",
    }


def test_vehicle_contract_normalizes_utc_heading_and_json_round_trips() -> None:
    vehicle = VehicleState.model_validate(vehicle_payload())
    assert vehicle.schema_version == "v1"
    assert vehicle.heading_deg == 0
    assert vehicle.ts.tzinfo == UTC
    restored = VehicleState.model_validate_json(vehicle.model_dump_json())
    assert restored == vehicle


def test_vehicle_rejects_invalid_coordinate_and_naive_timestamp() -> None:
    invalid_coordinate = vehicle_payload()
    invalid_coordinate["position"] = {"lat": 91, "lon": 77.5946}
    with pytest.raises(ValidationError):
        VehicleState.model_validate(invalid_coordinate)
    naive = vehicle_payload()
    naive["ts"] = "2026-09-04T10:00:00"
    with pytest.raises(ValidationError):
        VehicleState.model_validate(naive)


def test_hazard_and_event_envelope_have_versioned_json_contracts() -> None:
    hazard = Hazard(
        type=HazardType.POTHOLE,
        geometry=GeoJSONPoint(coordinates=(77.5946, 12.9716)),
        severity=0.8,
        confidence=0.6,
        first_seen=NOW,
        last_seen=NOW,
        ttl_s=300,
        source_ids=("obu-1",),
        evidence_count=1,
    )
    event = EventEnvelope[Hazard](
        event_type=EventType.HAZARD_UPDATED,
        produced_at=NOW,
        source_service="hazard-service",
        payload=hazard,
    )
    encoded = event.model_dump_json()
    restored = EventEnvelope[Hazard].model_validate_json(encoded)
    assert restored.payload.hazard_id == hazard.hazard_id
    assert isinstance(restored.event_id, UUID)


def test_alert_rejects_expiry_before_issue() -> None:
    with pytest.raises(ValidationError):
        Alert(
            risk_id="80b7e826-6913-471b-9b19-39b9c7d9fd1e",
            issued_at=NOW,
            expires_at=NOW - timedelta(seconds=1),
            priority=AlertPriority.CRITICAL,
            audience_actor_ids=("car-1",),
            confidence=0.8,
            summary="Collision risk ahead",
        )
