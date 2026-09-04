"""Tests for WrongWayDetector.

Validates detection of vehicles driving against the legal road direction,
including persistence requirements, speed gating, map-match confidence
filtering, and evidence correctness.
"""

from __future__ import annotations

from typing import Any

import pytest

from packages.safety_policies.config import PolicyConfig
from packages.schemas.canonical import RiskType
from services.safety_detectors.detectors.wrong_way import WrongWayDetector

from tests.safety.conftest import make_segment, make_vehicle_state
from tests.safety.fixtures.scenarios import create_wrong_way_scenario


class TestWrongWayDetection:
    """Wrong-way detector acceptance tests."""

    def _feed_sequence(
        self,
        detector: WrongWayDetector,
        scenario: dict[str, Any],
    ) -> list:
        """Evaluate multiple world-state snapshots and collect all risks."""
        all_risks = []
        for vehicles in scenario["vehicles_sequence"]:
            ws = {
                "vehicles": vehicles,
                "road_network": scenario["road_network"],
            }
            all_risks.extend(detector.evaluate(ws))
        return all_risks

    def test_clear_wrong_way_detection(self, policy_config: PolicyConfig) -> None:
        """Vehicle heading 180 deg against a 0 deg road should trigger risk
        after persistence threshold is met."""
        detector = WrongWayDetector(policy_config)
        scenario = create_wrong_way_scenario(
            road_direction_deg=0.0,
            vehicle_heading_deg=180.0,
            speed_mps=15.0,
            num_updates=5,
        )
        risks = self._feed_sequence(detector, scenario)
        assert len(risks) > 0
        assert all(r.type == RiskType.WRONG_WAY for r in risks)

    def test_no_false_positive_same_direction(self, policy_config: PolicyConfig) -> None:
        """Vehicle aligned with road direction should produce no risks."""
        detector = WrongWayDetector(policy_config)
        scenario = create_wrong_way_scenario(
            road_direction_deg=0.0,
            vehicle_heading_deg=0.0,
            speed_mps=15.0,
            num_updates=5,
        )
        risks = self._feed_sequence(detector, scenario)
        assert len(risks) == 0

    def test_persistence_requirement(self, policy_config: PolicyConfig) -> None:
        """A single wrong-way observation is not enough - must meet persistence."""
        detector = WrongWayDetector(policy_config)
        scenario = create_wrong_way_scenario(
            road_direction_deg=0.0,
            vehicle_heading_deg=180.0,
            speed_mps=15.0,
            num_updates=1,
        )
        risks = self._feed_sequence(detector, scenario)
        # With min_persistence_updates=3, a single update should not trigger.
        assert len(risks) == 0

    def test_low_speed_suppression(self, policy_config: PolicyConfig) -> None:
        """Vehicle below min_speed_mps should not trigger wrong-way,
        even at opposing heading."""
        detector = WrongWayDetector(policy_config)
        scenario = create_wrong_way_scenario(
            road_direction_deg=0.0,
            vehicle_heading_deg=180.0,
            speed_mps=0.5,  # Below default min_speed_mps=1.0
            num_updates=5,
        )
        risks = self._feed_sequence(detector, scenario)
        assert len(risks) == 0

    def test_low_map_match_confidence(self, policy_config: PolicyConfig) -> None:
        """If vehicle has no road_segment_id (map-match fails), no risk."""
        detector = WrongWayDetector(policy_config)
        seg_id = "nonexistent-segment"
        vs = make_vehicle_state(
            heading_deg=180.0,
            speed_mps=15.0,
            road_segment_id=seg_id,
        )
        ws = {
            "vehicles": [vs],
            "road_network": {
                "segments": [make_segment("seg-1", 0.0)],
            },
        }
        # Vehicle segment doesn't match any known segment -> None match
        all_risks = []
        for _ in range(5):
            all_risks.extend(detector.evaluate(ws))
        assert len(all_risks) == 0

    @pytest.mark.parametrize(
        "road_type, heading",
        [("HIGHWAY", 45.0), ("URBAN", 90.0)],
        ids=["highway-45.0", "urban-90.0"],
    )
    def test_different_road_types(
        self,
        policy_config: PolicyConfig,
        road_type: str,
        heading: float,
    ) -> None:
        """Wrong-way detection works on different road types."""
        detector = WrongWayDetector(policy_config)
        # Heading opposite to road direction: road dir + 180
        scenario = create_wrong_way_scenario(
            road_direction_deg=heading,
            vehicle_heading_deg=(heading + 180) % 360,
            speed_mps=15.0,
            num_updates=5,
            road_type=road_type,
        )
        risks = self._feed_sequence(detector, scenario)
        assert len(risks) > 0

    def test_evidence_fields(self, policy_config: PolicyConfig) -> None:
        """Evidence must contain alignment, heading, road direction, and position."""
        detector = WrongWayDetector(policy_config)
        scenario = create_wrong_way_scenario(
            road_direction_deg=0.0,
            vehicle_heading_deg=180.0,
            speed_mps=15.0,
            num_updates=5,
        )
        risks = self._feed_sequence(detector, scenario)
        assert len(risks) > 0
        ev = risks[0].evidence[0]
        assert ev["type"] == "wrong_way_detection"
        assert "heading_deg" in ev
        assert "road_direction_deg" in ev
        assert "alignment" in ev
        assert "position" in ev
        assert "lat" in ev["position"]
        assert "lon" in ev["position"]
