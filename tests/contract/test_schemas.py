"""Contract tests for Marga canonical schemas.

Every canonical schema must:
  1. Accept well-formed fixture data.
  2. Enforce field constraints (ranges, required fields).
  3. Reject clearly invalid payloads.
  4. Carry a ``schema_version`` on every SchemaVersioned subclass.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from marga_schemas.alert import Alert, AlertPriority, AlertState
from marga_schemas.common import (
    ActorType,
    ConnectivityState,
    GeoPoint,
    PositionMethod,
    SchemaVersioned,
    Source,
)
from marga_schemas.envelope import EventEnvelope
from marga_schemas.hazard import Hazard, HazardObservation, HazardState, HazardType
from marga_schemas.messaging import MessagePriority, V2XMessage
from marga_schemas.trust import SignedMessage, TrustAssessment, TrustLevel
from marga_schemas.vehicle import VehicleState


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

NOW = datetime.now(tz=timezone.utc)


def _vehicle_state_data() -> dict:
    return {
        "actor_id": "veh-001",
        "actor_type": ActorType.CAR,
        "ts": NOW.isoformat(),
        "position": {"lat": 12.9716, "lon": 77.5946},
        "position_uncertainty_m": 2.5,
        "speed_mps": 12.3,
        "heading_deg": 45.0,
        "source": Source.SIMULATION,
    }


def _hazard_observation_data() -> dict:
    return {
        "hazard_type": HazardType.POTHOLE,
        "position": {"lat": 13.0827, "lon": 80.2707},
        "observed_at": NOW.isoformat(),
        "source_id": "rsu-42",
        "detector_confidence": 0.85,
    }


def _hazard_data() -> dict:
    return {
        "hazard_type": HazardType.DEBRIS,
        "position": {"lat": 12.9716, "lon": 77.5946},
        "severity": 0.7,
        "confidence": 0.9,
        "first_seen": NOW.isoformat(),
        "last_seen": NOW.isoformat(),
        "ttl_s": 300,
        "evidence_count": 3,
        "state": HazardState.VERIFIED,
    }


def _alert_data() -> dict:
    return {
        "alert_type": "collision_warning",
        "priority": AlertPriority.HIGH,
        "state": AlertState.ACTIVE,
        "title": "Collision risk ahead",
        "description": "Two vehicles on convergent headings within 200 m",
        "confidence": 0.92,
        "position": {"lat": 12.9716, "lon": 77.5946},
        "affected_actor_ids": ["veh-001", "veh-002"],
    }


def _signed_message_data() -> dict:
    return {
        "sender_pseudonym": "pseudo-abc",
        "issued_at": NOW.isoformat(),
        "expires_at": NOW.isoformat(),
        "nonce": "nonce-123",
        "payload_hash": "sha256:abc123",
        "signature": "sig-xyz",
        "payload": {"msg": "test"},
    }


def _v2x_message_data() -> dict:
    return {
        "topic": "hazard.observed",
        "priority": MessagePriority.REGIONAL_SAFETY,
        "sender_id": "node-01",
        "timestamp": NOW.isoformat(),
        "ttl_s": 60,
        "payload": {"hazard_id": str(uuid4())},
    }


def _event_envelope_data() -> dict:
    return {
        "event_type": "hazard.observed",
        "source_service": "hazard-fusion",
        "payload": {"test": True},
    }


# ---------------------------------------------------------------------------
# 1. Valid fixture acceptance
# ---------------------------------------------------------------------------


class TestValidFixtures:
    """Every canonical schema must accept a well-formed fixture without error."""

    def test_vehicle_state(self) -> None:
        vs = VehicleState(**_vehicle_state_data())
        assert vs.actor_id == "veh-001"
        assert vs.speed_mps == 12.3

    def test_hazard_observation(self) -> None:
        ho = HazardObservation(**_hazard_observation_data())
        assert ho.hazard_type == HazardType.POTHOLE
        assert ho.detector_confidence == 0.85

    def test_hazard(self) -> None:
        h = Hazard(**_hazard_data())
        assert h.state == HazardState.VERIFIED
        assert h.evidence_count == 3

    def test_alert(self) -> None:
        a = Alert(**_alert_data())
        assert a.priority == AlertPriority.HIGH
        assert len(a.affected_actor_ids) == 2

    def test_signed_message(self) -> None:
        sm = SignedMessage(**_signed_message_data())
        assert sm.sender_pseudonym == "pseudo-abc"

    def test_v2x_message(self) -> None:
        v = V2XMessage(**_v2x_message_data())
        assert v.topic == "hazard.observed"
        assert v.ttl_s == 60

    def test_event_envelope(self) -> None:
        e = EventEnvelope(**_event_envelope_data())
        assert e.event_type == "hazard.observed"
        assert e.source_service == "hazard-fusion"


# ---------------------------------------------------------------------------
# 2. schema_version presence on all SchemaVersioned subclasses
# ---------------------------------------------------------------------------


class TestSchemaVersion:
    """Every SchemaVersioned model must carry schema_version."""

    @pytest.mark.parametrize(
        "model_cls,fixture_fn",
        [
            (VehicleState, _vehicle_state_data),
            (HazardObservation, _hazard_observation_data),
            (Hazard, _hazard_data),
            (Alert, _alert_data),
            (SignedMessage, _signed_message_data),
            (V2XMessage, _v2x_message_data),
        ],
    )
    def test_schema_version_present(self, model_cls: type, fixture_fn) -> None:
        obj = model_cls(**fixture_fn())
        assert hasattr(obj, "schema_version")
        assert isinstance(obj.schema_version, str)
        assert obj.schema_version  # non-empty

    def test_event_envelope_has_schema_version(self) -> None:
        e = EventEnvelope(**_event_envelope_data())
        assert hasattr(e, "schema_version")
        assert e.schema_version == "0.1.0"


# ---------------------------------------------------------------------------
# 3. Field constraint enforcement
# ---------------------------------------------------------------------------


class TestFieldConstraints:
    """Validate key domain invariants: ranges, signs, bounds."""

    # -- confidence must be 0..1 --
    def test_confidence_lower_bound(self) -> None:
        data = _hazard_observation_data()
        data["detector_confidence"] = 0.0
        HazardObservation(**data)  # should pass

    def test_confidence_upper_bound(self) -> None:
        data = _hazard_observation_data()
        data["detector_confidence"] = 1.0
        HazardObservation(**data)  # should pass

    # -- heading 0..360 --
    def test_heading_zero(self) -> None:
        data = _vehicle_state_data()
        data["heading_deg"] = 0.0
        VehicleState(**data)

    def test_heading_just_below_360(self) -> None:
        data = _vehicle_state_data()
        data["heading_deg"] = 359.99
        VehicleState(**data)

    # -- speed non-negative --
    def test_speed_zero(self) -> None:
        data = _vehicle_state_data()
        data["speed_mps"] = 0.0
        VehicleState(**data)

    # -- geo bounds --
    def test_lat_at_boundary(self) -> None:
        GeoPoint(lat=90.0, lon=0.0)
        GeoPoint(lat=-90.0, lon=0.0)

    def test_lon_at_boundary(self) -> None:
        GeoPoint(lat=0.0, lon=180.0)
        GeoPoint(lat=0.0, lon=-180.0)

    # -- ttl must be positive for V2XMessage --
    def test_v2x_ttl_minimum(self) -> None:
        data = _v2x_message_data()
        data["ttl_s"] = 1
        V2XMessage(**data)


# ---------------------------------------------------------------------------
# 4. Invalid data rejection
# ---------------------------------------------------------------------------


class TestInvalidDataRejection:
    """Confirm Pydantic rejects out-of-range or structurally invalid data."""

    def test_negative_confidence_rejected(self) -> None:
        data = _hazard_observation_data()
        data["detector_confidence"] = -0.1
        with pytest.raises(ValidationError):
            HazardObservation(**data)

    def test_confidence_above_one_rejected(self) -> None:
        data = _hazard_observation_data()
        data["detector_confidence"] = 1.01
        with pytest.raises(ValidationError):
            HazardObservation(**data)

    def test_lat_above_90_rejected(self) -> None:
        with pytest.raises(ValidationError):
            GeoPoint(lat=90.1, lon=0.0)

    def test_lat_below_minus90_rejected(self) -> None:
        with pytest.raises(ValidationError):
            GeoPoint(lat=-90.1, lon=0.0)

    def test_lon_above_180_rejected(self) -> None:
        with pytest.raises(ValidationError):
            GeoPoint(lat=0.0, lon=180.1)

    def test_negative_speed_rejected(self) -> None:
        data = _vehicle_state_data()
        data["speed_mps"] = -1.0
        with pytest.raises(ValidationError):
            VehicleState(**data)

    def test_heading_at_360_rejected(self) -> None:
        data = _vehicle_state_data()
        data["heading_deg"] = 360.0
        with pytest.raises(ValidationError):
            VehicleState(**data)

    def test_negative_heading_rejected(self) -> None:
        data = _vehicle_state_data()
        data["heading_deg"] = -1.0
        with pytest.raises(ValidationError):
            VehicleState(**data)

    def test_negative_position_uncertainty_rejected(self) -> None:
        data = _vehicle_state_data()
        data["position_uncertainty_m"] = -0.5
        with pytest.raises(ValidationError):
            VehicleState(**data)

    def test_v2x_ttl_zero_rejected(self) -> None:
        data = _v2x_message_data()
        data["ttl_s"] = 0
        with pytest.raises(ValidationError):
            V2XMessage(**data)

    def test_missing_required_field_rejected(self) -> None:
        data = _vehicle_state_data()
        del data["actor_id"]
        with pytest.raises(ValidationError):
            VehicleState(**data)

    def test_alert_negative_confidence_rejected(self) -> None:
        data = _alert_data()
        data["confidence"] = -0.5
        with pytest.raises(ValidationError):
            Alert(**data)

    def test_hazard_severity_above_one_rejected(self) -> None:
        data = _hazard_data()
        data["severity"] = 1.5
        with pytest.raises(ValidationError):
            Hazard(**data)
