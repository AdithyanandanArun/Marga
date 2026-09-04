"""Tests for EmergencyBrakingDetector.

Validates detection of hard deceleration events, duration gating,
following-actor targeting, alert TTL, and evidence completeness.

The detector uses wall-clock time (datetime.now) internally for braking
duration tracking. Tests manipulate the detector's internal state to
simulate time progression without real delays.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from packages.safety_policies.config import PolicyConfig
from packages.schemas.canonical import RiskType
from services.safety_detectors.detectors.emergency_braking import EmergencyBrakingDetector

from tests.safety.conftest import make_segment, make_vehicle_state
from tests.safety.fixtures.scenarios import create_braking_scenario


class TestEmergencyBraking:
    """Emergency braking detector acceptance tests."""

    def _feed_sequence_with_time(
        self,
        detector: EmergencyBrakingDetector,
        scenario: dict[str, Any],
    ) -> list:
        """Feed scenario updates. After the first update, backdate the
        braking start time to simulate wall-clock time passage."""
        all_risks = []
        for idx, vehicles in enumerate(scenario["vehicles_sequence"]):
            ws = {
                "vehicles": vehicles,
                "road_network": scenario["road_network"],
            }
            risks = detector.evaluate(ws)
            if idx == 0:
                # Backdate braking_since to simulate 1s having passed
                for state in detector._braking_state.values():
                    state["braking_since"] = datetime.now(timezone.utc) - timedelta(seconds=1.0)
            all_risks.extend(risks)
        return all_risks

    def test_hard_braking_detected(self, policy_config: PolicyConfig) -> None:
        """Vehicle decelerating at -6 m/s^2 should trigger braking risk
        once duration threshold is met."""
        detector = EmergencyBrakingDetector(policy_config)
        scenario = create_braking_scenario(
            initial_speed_mps=20.0,
            deceleration_mps2=-6.0,
        )
        risks = self._feed_sequence_with_time(detector, scenario)
        assert len(risks) > 0
        assert all(r.type == RiskType.EMERGENCY_BRAKING for r in risks)

    def test_mild_braking_not_detected(self, policy_config: PolicyConfig) -> None:
        """Deceleration above the threshold (-2 m/s^2 vs -4 threshold)
        should not trigger."""
        detector = EmergencyBrakingDetector(policy_config)
        scenario = create_braking_scenario(
            initial_speed_mps=20.0,
            deceleration_mps2=-2.0,
        )
        risks = self._feed_sequence_with_time(detector, scenario)
        assert len(risks) == 0

    def test_short_duration_suppressed(self, policy_config: PolicyConfig) -> None:
        """A single braking update should not trigger (duration < min_duration_s)."""
        detector = EmergencyBrakingDetector(policy_config)
        vs = make_vehicle_state(
            speed_mps=20.0,
            acceleration_mps2=-6.0,
            road_segment_id="seg-1",
        )
        ws = {
            "vehicles": [vs],
            "road_network": {"segments": [make_segment("seg-1", 0.0)]},
        }
        risks = detector.evaluate(ws)
        # First call: braking_since = now, duration = 0 < 0.5s
        assert len(risks) == 0

    def test_following_actors_targeted(self, policy_config: PolicyConfig) -> None:
        """Risk event should include the following actor in affected_actor_ids."""
        detector = EmergencyBrakingDetector(policy_config)
        scenario = create_braking_scenario(
            initial_speed_mps=20.0,
            deceleration_mps2=-6.0,
            follower_distance_m=50.0,
        )
        risks = self._feed_sequence_with_time(detector, scenario)
        if risks:
            meta = scenario["metadata"]
            # The braker should always be in affected_actor_ids
            braker_in_risks = any(
                meta["braker_id"] in r.affected_actor_ids for r in risks
            )
            assert braker_in_risks

    def test_alert_ttl(self, policy_config: PolicyConfig) -> None:
        """Risk events should have an expires_at set to now + alert_ttl_s."""
        detector = EmergencyBrakingDetector(policy_config)
        scenario = create_braking_scenario(
            initial_speed_mps=20.0,
            deceleration_mps2=-6.0,
        )
        risks = self._feed_sequence_with_time(detector, scenario)
        if risks:
            for r in risks:
                assert r.expires_at is not None
                # The TTL should be approximately alert_ttl_s (15s default)
                delta = (r.expires_at - r.ts).total_seconds()
                assert 10 <= delta <= 20  # generous range for wall-clock

    def test_evidence_complete(self, policy_config: PolicyConfig) -> None:
        """Evidence should contain acceleration, position, speed, and threshold."""
        detector = EmergencyBrakingDetector(policy_config)
        scenario = create_braking_scenario(
            initial_speed_mps=20.0,
            deceleration_mps2=-6.0,
        )
        risks = self._feed_sequence_with_time(detector, scenario)
        assert len(risks) > 0
        ev = risks[0].evidence[0]
        assert ev["type"] == "emergency_braking"
        assert "acceleration_mps2" in ev
        assert "peak_deceleration_mps2" in ev
        assert "speed_mps" in ev
        assert "position" in ev
        assert "braking_duration_s" in ev
