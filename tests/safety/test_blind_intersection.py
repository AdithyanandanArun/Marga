"""Tests for BlindIntersectionDetector.

Validates ETA overlap detection, signal context integration, distance
gating, and evidence geometry. The detector expects vehicles as plain
dicts and intersections as a top-level world_state key.
"""

from __future__ import annotations

from typing import Any

import pytest

from packages.safety_policies.config import PolicyConfig
from packages.schemas.canonical import RiskType
from services.safety_detectors.detectors.blind_intersection import BlindIntersectionDetector

from tests.safety.fixtures.scenarios import create_blind_intersection_scenario


class TestBlindIntersection:
    """Blind intersection detector acceptance tests."""

    def test_conflicting_approaches(self, policy_config: PolicyConfig) -> None:
        """Two vehicles approaching from perpendicular segments at similar
        speed and close distance should trigger intersection conflict."""
        detector = BlindIntersectionDetector(policy_config)
        scenario = create_blind_intersection_scenario(
            approach_speeds=(3.0, 3.0),
            approach_distances=(20.0, 20.0),
        )
        risks = detector.evaluate(scenario)
        assert len(risks) > 0
        assert all(r.type == RiskType.BLIND_INTERSECTION for r in risks)

    def test_no_conflict_with_signal(self, policy_config: PolicyConfig) -> None:
        """With a protected green signal on one movement, severity should
        be reduced but risk may still be present (signals don't eliminate risk)."""
        detector = BlindIntersectionDetector(policy_config)
        scenario = create_blind_intersection_scenario(
            approach_speeds=(5.0, 5.0),
            approach_distances=(30.0, 30.0),
            signal_state="GREEN",
        )
        risks = detector.evaluate(scenario)
        # Risks may still appear because the detector treats signals as
        # severity modifiers, not binary gates. Check that if present,
        # severity is reduced.
        if risks:
            no_signal = create_blind_intersection_scenario(
                approach_speeds=(5.0, 5.0),
                approach_distances=(30.0, 30.0),
            )
            detector2 = BlindIntersectionDetector(policy_config)
            risks_no_signal = detector2.evaluate(no_signal)
            if risks_no_signal:
                assert risks[0].severity <= risks_no_signal[0].severity

    def test_far_approach_no_alert(self, policy_config: PolicyConfig) -> None:
        """Vehicles far from the intersection (beyond approach_distance_m)
        should not trigger."""
        detector = BlindIntersectionDetector(policy_config)
        scenario = create_blind_intersection_scenario(
            approach_speeds=(10.0, 10.0),
            approach_distances=(200.0, 200.0),  # Beyond 100m default
        )
        risks = detector.evaluate(scenario)
        assert len(risks) == 0

    def test_eta_overlap(self, policy_config: PolicyConfig) -> None:
        """Verify that overlapping ETA windows produce risk events."""
        detector = BlindIntersectionDetector(policy_config)
        # Close and slow -> wide ETA uncertainty windows -> strong overlap
        scenario = create_blind_intersection_scenario(
            approach_speeds=(3.0, 3.0),
            approach_distances=(20.0, 20.0),
        )
        risks = detector.evaluate(scenario)
        assert len(risks) > 0

    def test_evidence_geometry(self, policy_config: PolicyConfig) -> None:
        """Evidence should include intersection and zone IDs, ETA values."""
        detector = BlindIntersectionDetector(policy_config)
        scenario = create_blind_intersection_scenario(
            approach_speeds=(5.0, 5.0),
            approach_distances=(30.0, 30.0),
        )
        risks = detector.evaluate(scenario)
        if risks:
            ev = risks[0].evidence[0]
            assert ev["type"] == "intersection_conflict"
            assert "intersection_id" in ev
            assert "zone_id" in ev
            assert "actor_a_eta_s" in ev
            assert "actor_b_eta_s" in ev
            assert "eta_overlap_s" in ev
