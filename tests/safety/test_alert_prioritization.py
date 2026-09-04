"""Tests for AlertPrioritizer.

Validates critical pre-emption, suppression windows, max concurrent
alert enforcement, hysteresis, alert lifecycle, and evidence handling.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from packages.safety_policies.config import PolicyConfig
from packages.schemas.canonical import (
    Alert,
    AlertLevel,
    AlertStatus,
    RiskEvent,
    RiskType,
)
from services.safety_detectors.detectors.alert_prioritization import AlertPrioritizer

from tests.safety.conftest import make_risk_event


class TestAlertPrioritization:
    """Alert prioritizer acceptance tests."""

    def test_critical_preempts_advisory(self, policy_config: PolicyConfig) -> None:
        """A critical collision risk should suppress advisory-level alerts
        for the same actors."""
        prioritizer = AlertPrioritizer(policy_config)
        actor_id = "actor-1"
        now = datetime.now(timezone.utc)

        # Actor states with vulnerable actor type (PEDESTRIAN has weight 1.5)
        # and high speed (makes braking infeasible)
        actor_states = {
            actor_id: {
                "actor_type": "PEDESTRIAN",
                "speed_mps": 30.0,
            },
        }

        # First: create an advisory-level risk
        advisory_risk = make_risk_event(
            risk_type=RiskType.ROAD_HAZARD,
            affected_actor_ids=[actor_id],
            severity=0.3,
            confidence=0.5,
            road_segment_id="seg-1",
            time_to_conflict_s=20.0,
        )

        # Create a critical collision risk (maximum severity, very short ttc,
        # high risk_score)
        critical_risk = make_risk_event(
            risk_type=RiskType.WRONG_WAY,
            affected_actor_ids=[actor_id],
            severity=1.0,
            confidence=1.0,
            risk_score=1.0,
            road_segment_id="seg-1",
            time_to_conflict_s=1.0,
            evidence=[{"type": "wrong_way_detection"}],
        )

        # Create alerts through evaluate_risk
        alert_advisory = prioritizer.evaluate_risk(
            advisory_risk, {"actor_states": actor_states, "active_alerts": []}
        )
        assert alert_advisory is not None

        alert_critical = prioritizer.evaluate_risk(
            critical_risk,
            {
                "actor_states": actor_states,
                "active_alerts": [alert_advisory] if alert_advisory else [],
            },
        )
        assert alert_critical is not None
        assert alert_critical.level == AlertLevel.CRITICAL

    def test_suppression_window(self, policy_config: PolicyConfig) -> None:
        """Duplicate risks within the suppression window should be suppressed."""
        prioritizer = AlertPrioritizer(policy_config)
        risk = make_risk_event(
            risk_type=RiskType.STALLED_VEHICLE,
            affected_actor_ids=["a1"],
            severity=0.5,
            confidence=0.7,
            road_segment_id="seg-1",
        )
        alert1 = prioritizer.evaluate_risk(risk, {"actor_states": {}, "active_alerts": []})
        assert alert1 is not None

        # Same risk immediately after should be suppressed
        alert2 = prioritizer.evaluate_risk(risk, {"actor_states": {}, "active_alerts": []})
        assert alert2 is None

    def test_max_concurrent_alerts(self, policy_config: PolicyConfig) -> None:
        """No actor should receive more than max_concurrent_alerts active alerts."""
        prioritizer = AlertPrioritizer(policy_config)
        actor_id = "actor-busy"

        alerts = []
        for i in range(policy_config.alert_prioritization.max_concurrent_alerts + 2):
            risk = make_risk_event(
                risk_type=RiskType.ROAD_HAZARD,
                affected_actor_ids=[actor_id],
                severity=0.3 + i * 0.05,
                confidence=0.6,
                road_segment_id=f"seg-{i}",
            )
            alert = prioritizer.evaluate_risk(
                risk, {"actor_states": {}, "active_alerts": alerts}
            )
            if alert is not None:
                alerts.append(alert)

        # Active alerts for actor should not exceed max_concurrent
        active = [a for a in alerts if a.status == AlertStatus.ACTIVE]
        assert len(active) <= policy_config.alert_prioritization.max_concurrent_alerts

    def test_hysteresis(self, policy_config: PolicyConfig) -> None:
        """Small risk score changes should not create new alerts."""
        prioritizer = AlertPrioritizer(policy_config)
        risk1 = make_risk_event(
            risk_type=RiskType.ROAD_HAZARD,
            affected_actor_ids=["a1"],
            severity=0.5,
            confidence=0.7,
            risk_score=0.35,
            road_segment_id="seg-1",
        )
        alert1 = prioritizer.evaluate_risk(risk1, {"actor_states": {}, "active_alerts": []})
        assert alert1 is not None

        # Small change (within hysteresis threshold of 0.1)
        risk2 = make_risk_event(
            risk_type=RiskType.ROAD_HAZARD,
            affected_actor_ids=["a1"],
            severity=0.52,
            confidence=0.7,
            risk_score=0.364,
            road_segment_id="seg-1",
        )
        alert2 = prioritizer.evaluate_risk(risk2, {"actor_states": {}, "active_alerts": [alert1]})
        # Should be suppressed by hysteresis (score changed by only 0.014)
        assert alert2 is None

    def test_alert_lifecycle(self, policy_config: PolicyConfig) -> None:
        """Alerts should be created as ACTIVE and can be resolved."""
        prioritizer = AlertPrioritizer(policy_config)
        risk = make_risk_event(
            risk_type=RiskType.EMERGENCY_BRAKING,
            affected_actor_ids=["a1"],
            severity=0.7,
            confidence=0.8,
            road_segment_id="seg-1",
        )
        alert = prioritizer.evaluate_risk(risk, {"actor_states": {}, "active_alerts": []})
        assert alert is not None
        assert alert.status == AlertStatus.ACTIVE

        # Resolve
        resolved = prioritizer.resolve_risk(risk.risk_id)
        assert resolved is not None
        assert resolved.status == AlertStatus.RESOLVED

    def test_evidence_in_alerts(self, policy_config: PolicyConfig) -> None:
        """Alerts should carry forward evidence from the risk event,
        plus machine reasoning evidence from the prioritizer."""
        prioritizer = AlertPrioritizer(policy_config)
        original_evidence = [{"type": "test_detection", "detail": "unit-test"}]
        risk = make_risk_event(
            risk_type=RiskType.ANIMAL_CROSSING,
            affected_actor_ids=["a1"],
            severity=0.6,
            confidence=0.7,
            evidence=original_evidence,
            road_segment_id="seg-1",
        )
        alert = prioritizer.evaluate_risk(risk, {"actor_states": {}, "active_alerts": []})
        assert alert is not None
        # Should have original evidence + machine reasoning
        assert len(alert.evidence) >= 2
        types = [e.get("type") for e in alert.evidence]
        assert "test_detection" in types
        assert "prioritization_reasoning" in types
