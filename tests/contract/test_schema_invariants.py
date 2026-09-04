"""Contract tests: verify schema invariants for all canonical Marga types."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

UTC = timezone.utc

_GOOD_POSITION = {
    "lat": 12.9716,
    "lon": 77.5946,
    "alt_m": None,
    "uncertainty_m": 2.5,
    "confidence": 0.95,
    "source": "gnss",
}


def _now() -> datetime:
    return datetime.now(UTC)


def _make_vehicle_state(**overrides):
    from packages.schemas.actors import VehicleState, VehicleType
    from packages.schemas.common import PositionEstimate

    defaults = {
        "vehicle_id": "v-001",
        "timestamp_utc": _now(),
        "position": PositionEstimate(**_GOOD_POSITION),
        "speed_mps": 10.0,
        "heading_deg": 90.0,
        "vehicle_type": VehicleType.car,
        "source": "sumo_traci",
    }
    defaults.update(overrides)
    return VehicleState(**defaults)


# ---------------------------------------------------------------------------
# VehicleState invariants
# ---------------------------------------------------------------------------


class TestVehicleStateInvariants:
    def test_rejects_negative_speed(self):
        with pytest.raises(ValidationError, match="speed_mps"):
            _make_vehicle_state(speed_mps=-1.0)

    def test_accepts_zero_speed(self):
        vs = _make_vehicle_state(speed_mps=0.0)
        assert vs.speed_mps == 0.0

    def test_rejects_heading_below_zero(self):
        with pytest.raises(ValidationError, match="heading_deg"):
            _make_vehicle_state(heading_deg=-1.0)

    def test_rejects_heading_above_360(self):
        with pytest.raises(ValidationError, match="heading_deg"):
            _make_vehicle_state(heading_deg=360.1)

    def test_accepts_heading_at_boundaries(self):
        vs0 = _make_vehicle_state(heading_deg=0.0)
        vs360 = _make_vehicle_state(heading_deg=360.0)
        assert vs0.heading_deg == 0.0
        assert vs360.heading_deg == 360.0

    def test_has_required_fields(self):
        vs = _make_vehicle_state()
        assert hasattr(vs, "schema_version")
        assert hasattr(vs, "timestamp_utc")
        assert hasattr(vs, "trace_id")
        assert vs.schema_version == "1.0"
        assert vs.trace_id  # non-empty UUID


# ---------------------------------------------------------------------------
# PositionEstimate invariants
# ---------------------------------------------------------------------------


class TestPositionEstimateInvariants:
    def test_rejects_confidence_below_zero(self):
        from packages.schemas.common import PositionEstimate

        with pytest.raises(ValidationError, match="confidence"):
            PositionEstimate(
                lat=12.9, lon=77.5, uncertainty_m=1.0, confidence=-0.1, source="gnss"
            )

    def test_rejects_confidence_above_one(self):
        from packages.schemas.common import PositionEstimate

        with pytest.raises(ValidationError, match="confidence"):
            PositionEstimate(
                lat=12.9, lon=77.5, uncertainty_m=1.0, confidence=1.01, source="gnss"
            )

    def test_accepts_confidence_boundaries(self):
        from packages.schemas.common import PositionEstimate

        pe0 = PositionEstimate(lat=12.9, lon=77.5, uncertainty_m=1.0, confidence=0.0, source="x")
        pe1 = PositionEstimate(lat=12.9, lon=77.5, uncertainty_m=1.0, confidence=1.0, source="x")
        assert pe0.confidence == 0.0
        assert pe1.confidence == 1.0


# ---------------------------------------------------------------------------
# Round-trip JSON serialisation for all canonical types
# ---------------------------------------------------------------------------


class TestRoundTripSerialisation:
    def _roundtrip(self, instance):
        raw = instance.model_dump_json()
        restored = type(instance).model_validate_json(raw)
        assert instance.model_dump() == restored.model_dump()

    def test_vehicle_state_roundtrip(self):
        self._roundtrip(_make_vehicle_state())

    def test_pedestrian_state_roundtrip(self):
        from packages.schemas.actors import PedestrianState
        from packages.schemas.common import PositionEstimate

        ps = PedestrianState(
            pedestrian_id="p-001",
            timestamp_utc=_now(),
            position=PositionEstimate(**_GOOD_POSITION),
            speed_mps=1.2,
            heading_deg=180.0,
            source="sumo_traci",
        )
        self._roundtrip(ps)

    def test_dynamic_actor_roundtrip(self):
        from packages.schemas.actors import DynamicActorObservation
        from packages.schemas.common import PositionEstimate

        obs = DynamicActorObservation(
            actor_id="a-001",
            timestamp_utc=_now(),
            actor_type="animal",
            position=PositionEstimate(**_GOOD_POSITION),
            confidence=0.7,
            source="camera",
        )
        self._roundtrip(obs)

    def test_infrastructure_state_roundtrip(self):
        from packages.schemas.common import Position
        from packages.schemas.infrastructure import (
            InfrastructureState,
            InfrastructureType,
            SignalPhase,
        )

        infra = InfrastructureState(
            infrastructure_id="sig-001",
            timestamp_utc=_now(),
            infrastructure_type=InfrastructureType.traffic_signal,
            position=Position(lat=12.9, lon=77.5),
            signal_phase=SignalPhase.green,
            phase_remaining_s=20.0,
            source="tlc",
        )
        self._roundtrip(infra)

    def test_road_state_roundtrip(self):
        from packages.schemas.road import RoadCondition, RoadState

        rs = RoadState(
            edge_id="edge-001",
            timestamp_utc=_now(),
            lanes_available=2,
            total_lanes=3,
            speed_limit_mps=13.9,
            road_condition=RoadCondition.wet,
            source="sumo",
        )
        self._roundtrip(rs)

    def test_road_event_roundtrip(self):
        from packages.schemas.road import RoadEvent

        re = RoadEvent(
            edge_id="edge-002",
            event_type="closure",
            start_time_utc=_now(),
            severity=0.8,
            description="Road blocked due to flooding",
            source="field_report",
        )
        self._roundtrip(re)

    def test_hazard_observation_roundtrip(self):
        from packages.schemas.common import PositionEstimate
        from packages.schemas.hazards import HazardObservation

        h = HazardObservation(
            hazard_id="h-001",
            timestamp_utc=_now(),
            hazard_type="pothole",
            position=PositionEstimate(**_GOOD_POSITION),
            confidence=0.85,
            reporting_source="obu",
        )
        self._roundtrip(h)

    def test_alert_roundtrip(self):
        from packages.schemas.alerts import Alert, AlertSeverity

        a = Alert(
            timestamp_utc=_now(),
            severity=AlertSeverity.warning,
            alert_type="collision_risk",
            affected_actor_ids=["v-001", "v-002"],
            confidence=0.9,
            evidence={"ttc_s": 1.2},
            policy_version="1.0.0",
        )
        self._roundtrip(a)

    def test_canonical_event_roundtrip(self):
        from packages.schemas.events import ALERT_ISSUED, CanonicalEvent

        ev = CanonicalEvent(
            event_type=ALERT_ISSUED,
            timestamp_utc=_now(),
            source="risk-engine",
            payload={"alert_id": "a-001"},
        )
        self._roundtrip(ev)


# ---------------------------------------------------------------------------
# CanonicalEvent factory methods
# ---------------------------------------------------------------------------


class TestCanonicalEventFactories:
    def test_from_vehicle_state(self):
        from packages.schemas.events import ACTOR_STATE_UPDATED, CanonicalEvent

        vs = _make_vehicle_state()
        ev = CanonicalEvent.from_vehicle_state(vs)
        assert ev.event_type == ACTOR_STATE_UPDATED
        assert ev.payload["vehicle_id"] == vs.vehicle_id
        assert ev.trace_id == vs.trace_id

    def test_from_pedestrian_state(self):
        from packages.schemas.actors import PedestrianState
        from packages.schemas.common import PositionEstimate
        from packages.schemas.events import ACTOR_STATE_UPDATED, CanonicalEvent

        ps = PedestrianState(
            pedestrian_id="p-999",
            timestamp_utc=_now(),
            position=PositionEstimate(**_GOOD_POSITION),
            speed_mps=0.8,
            heading_deg=270.0,
            source="sumo_traci",
        )
        ev = CanonicalEvent.from_pedestrian_state(ps)
        assert ev.event_type == ACTOR_STATE_UPDATED
        assert ev.payload["pedestrian_id"] == "p-999"

    def test_from_infrastructure_state(self):
        from packages.schemas.common import Position
        from packages.schemas.events import INFRASTRUCTURE_SIGNAL_UPDATED, CanonicalEvent
        from packages.schemas.infrastructure import (
            InfrastructureState,
            InfrastructureType,
            SignalPhase,
        )

        infra = InfrastructureState(
            infrastructure_id="sig-002",
            timestamp_utc=_now(),
            infrastructure_type=InfrastructureType.traffic_signal,
            position=Position(lat=12.9, lon=77.5),
            signal_phase=SignalPhase.red,
            source="tlc",
        )
        ev = CanonicalEvent.from_infrastructure_state(infra)
        assert ev.event_type == INFRASTRUCTURE_SIGNAL_UPDATED
        assert ev.payload["infrastructure_id"] == "sig-002"

    def test_from_road_state(self):
        from packages.schemas.events import ROAD_STATE_UPDATED, CanonicalEvent
        from packages.schemas.road import RoadState

        rs = RoadState(
            edge_id="edge-003",
            timestamp_utc=_now(),
            lanes_available=1,
            total_lanes=2,
            speed_limit_mps=8.3,
            source="sumo",
        )
        ev = CanonicalEvent.from_road_state(rs)
        assert ev.event_type == ROAD_STATE_UPDATED
        assert ev.payload["edge_id"] == "edge-003"

    def test_factory_events_are_valid_canonical_events(self):
        """All factory-produced events must satisfy CanonicalEvent validation."""
        from packages.schemas.events import CanonicalEvent

        vs = _make_vehicle_state()
        ev = CanonicalEvent.from_vehicle_state(vs)
        # Re-validate by round-tripping through JSON
        raw = ev.model_dump_json()
        restored = CanonicalEvent.model_validate_json(raw)
        assert restored.event_id == ev.event_id
