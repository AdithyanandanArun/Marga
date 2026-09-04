"""Contract tests for canonical schema validation.

Verifies that domain entities enforce their invariants correctly and
that all required fields are present and validated.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from packages.schemas.canonical import (
    Alert,
    AlertLevel,
    AlertStatus,
    Hazard,
    HazardState,
    HazardType,
    Position,
    RiskEvent,
    RiskType,
    SourceType,
    VehicleState,
)


class TestVehicleStateValidation:
    """Contract tests for VehicleState."""

    def test_vehicle_state_valid(self) -> None:
        """Valid VehicleState passes validation."""
        vs = VehicleState(
            ts=datetime.now(timezone.utc),
            position=Position(lat=12.9716, lon=77.5946),
            position_uncertainty_m=2.0,
            speed_mps=10.0,
            heading_deg=45.0,
        )
        assert vs.heading_deg == 45.0
        assert vs.speed_mps == 10.0
        assert vs.position.lat == 12.9716
        assert vs.schema_version is not None

    def test_vehicle_state_invalid_heading(self) -> None:
        """Heading >= 360 should be rejected by Field constraint."""
        with pytest.raises(Exception):
            VehicleState(
                ts=datetime.now(timezone.utc),
                position=Position(lat=12.9716, lon=77.5946),
                position_uncertainty_m=2.0,
                speed_mps=10.0,
                heading_deg=370.0,
            )

    def test_vehicle_state_negative_speed_rejected(self) -> None:
        """Negative speed should be rejected."""
        with pytest.raises(Exception):
            VehicleState(
                ts=datetime.now(timezone.utc),
                position=Position(lat=12.9716, lon=77.5946),
                position_uncertainty_m=2.0,
                speed_mps=-5.0,
                heading_deg=0.0,
            )

    def test_vehicle_state_default_fields(self) -> None:
        """Default fields are populated correctly."""
        vs = VehicleState(
            ts=datetime.now(timezone.utc),
            position=Position(lat=0.0, lon=0.0),
            position_uncertainty_m=0.0,
            speed_mps=0.0,
            heading_deg=0.0,
        )
        assert vs.actor_id is not None
        assert len(vs.actor_id) > 0
        assert vs.source == SourceType.SIMULATION
        assert vs.schema_version is not None


class TestRiskEventValidation:
    """Contract tests for RiskEvent."""

    def test_risk_event_evidence_required(self) -> None:
        """RiskEvent can be created with evidence."""
        evidence = [
            {"type": "test", "detail": "contract test evidence"},
        ]
        risk = RiskEvent(
            type=RiskType.COLLISION,
            ts=datetime.now(timezone.utc),
            affected_actor_ids=["actor-1"],
            severity=0.8,
            confidence=0.9,
            risk_score=0.72,
            evidence=evidence,
        )
        assert len(risk.evidence) == 1
        assert risk.evidence[0]["type"] == "test"
        assert risk.risk_id is not None

    def test_risk_event_severity_bounds(self) -> None:
        """Severity must be in [0, 1]."""
        with pytest.raises(Exception):
            RiskEvent(
                type=RiskType.COLLISION,
                ts=datetime.now(timezone.utc),
                affected_actor_ids=["actor-1"],
                severity=1.5,
                confidence=0.9,
                risk_score=1.35,
            )

    def test_risk_event_all_risk_types(self) -> None:
        """All RiskType values can be used to create a RiskEvent."""
        for rt in RiskType:
            risk = RiskEvent(
                type=rt,
                ts=datetime.now(timezone.utc),
                affected_actor_ids=["actor-1"],
                severity=0.5,
                confidence=0.5,
                risk_score=0.25,
            )
            assert risk.type == rt


class TestAlertValidation:
    """Contract tests for Alert."""

    def test_alert_complete(self) -> None:
        """Alert has all required fields."""
        alert = Alert(
            risk_id="risk-1",
            level=AlertLevel.WARNING,
            status=AlertStatus.ACTIVE,
            title="Test Alert",
            description="This is a test alert",
            ts=datetime.now(timezone.utc),
            affected_actor_ids=["actor-1"],
            confidence=0.85,
            evidence=[{"type": "test"}],
        )
        assert alert.alert_id is not None
        assert alert.risk_id == "risk-1"
        assert alert.level == AlertLevel.WARNING
        assert alert.status == AlertStatus.ACTIVE
        assert alert.title == "Test Alert"
        assert alert.description
        assert alert.ts is not None
        assert len(alert.affected_actor_ids) == 1
        assert alert.confidence == 0.85
        assert len(alert.evidence) == 1
        assert alert.schema_version is not None

    def test_alert_all_levels(self) -> None:
        """All AlertLevel values are valid."""
        for level in AlertLevel:
            alert = Alert(
                risk_id="risk-1",
                level=level,
                title="Test",
                description="Test",
                ts=datetime.now(timezone.utc),
                affected_actor_ids=["a"],
                confidence=0.5,
            )
            assert alert.level == level

    def test_alert_all_statuses(self) -> None:
        """All AlertStatus values are valid."""
        for status in AlertStatus:
            alert = Alert(
                risk_id="risk-1",
                level=AlertLevel.ADVISORY,
                status=status,
                title="Test",
                description="Test",
                ts=datetime.now(timezone.utc),
                affected_actor_ids=["a"],
                confidence=0.5,
            )
            assert alert.status == status


class TestHazardValidation:
    """Contract tests for Hazard."""

    def test_hazard_lifecycle_states(self) -> None:
        """All HazardState values are valid enum members."""
        expected = {"CANDIDATE", "VERIFIED", "STALE", "EXPIRED"}
        actual = {s.value for s in HazardState}
        assert actual == expected

    def test_hazard_creation(self) -> None:
        """Hazard can be created with all required fields."""
        now = datetime.now(timezone.utc)
        hazard = Hazard(
            type=HazardType.POTHOLE,
            geometry={"type": "Point", "coordinates": [77.5946, 12.9716]},
            severity=0.5,
            confidence=0.8,
            first_seen=now,
            last_seen=now,
            ttl_s=3600,
            source_ids=["src-1"],
            evidence_count=1,
            state=HazardState.CANDIDATE,
        )
        assert hazard.hazard_id is not None
        assert hazard.type == HazardType.POTHOLE
        assert hazard.state == HazardState.CANDIDATE

    @pytest.mark.parametrize("state", list(HazardState))
    def test_hazard_all_states_valid(self, state: HazardState) -> None:
        """Each HazardState value can be assigned to a Hazard."""
        now = datetime.now(timezone.utc)
        hazard = Hazard(
            type=HazardType.DEBRIS,
            geometry={"type": "Point", "coordinates": [0, 0]},
            severity=0.3,
            confidence=0.5,
            first_seen=now,
            last_seen=now,
            ttl_s=1800,
            state=state,
        )
        assert hazard.state == state
