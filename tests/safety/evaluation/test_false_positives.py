"""Evaluation tests for false positive rates.

Tests ensure that detectors do not produce false positives in scenarios
where no risk should be detected.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from packages.safety_policies.config import PolicyConfig
from services.safety_detectors.detectors.wrong_way import WrongWayDetector
from services.safety_detectors.detectors.stalled_vehicle import StalledVehicleDetector
from services.safety_detectors.detectors.animal_conflict import AnimalConflictDetector

from tests.safety.conftest import make_segment, make_vehicle_state
from tests.safety.fixtures.scenarios import (
    create_wrong_way_scenario,
    create_stalled_scenario,
    create_animal_crossing_scenario,
)


class TestWrongWayFalsePositiveRate:
    """Wrong-way detector should not fire for compliant vehicles."""

    def test_wrong_way_false_positive_rate(self, policy_config: PolicyConfig) -> None:
        """Run many normal-direction scenarios and verify zero false positives."""
        detector = WrongWayDetector(policy_config)
        false_positives = 0
        n_trials = 50

        for _ in range(n_trials):
            scenario = create_wrong_way_scenario(
                road_direction_deg=0.0,
                vehicle_heading_deg=0.0,
                speed_mps=15.0,
                num_updates=5,
            )
            risks = []
            for vehicles in scenario["vehicles_sequence"]:
                ws = {
                    "vehicles": vehicles,
                    "road_network": scenario["road_network"],
                }
                risks.extend(detector.evaluate(ws))
            if risks:
                false_positives += 1
            # Reset internal state between trials
            detector._persistence.clear()

        assert false_positives == 0, (
            f"Expected 0 false positives, got {false_positives}/{n_trials}"
        )


class TestStalledVehicleFalsePositiveInCongestion:
    """Stalled vehicle detector should not trigger during congestion."""

    def test_stalled_vehicle_false_positive_in_congestion(
        self, policy_config: PolicyConfig,
    ) -> None:
        """When surrounding traffic is also stopped (congestion), the
        detector should not flag a stopped vehicle as stalled."""
        detector = StalledVehicleDetector(policy_config)
        scenario = create_stalled_scenario(
            surrounding_flow_mps=0.0,
        )
        # Seed stopped state
        detector.evaluate(scenario)
        # Backdate to exceed threshold
        for state in detector._stopped_state.values():
            state["stopped_since"] = datetime.now(timezone.utc) - timedelta(seconds=120.0)
        # Evaluate again - should not trigger because surrounding flow < threshold
        risks = detector.evaluate(scenario)
        assert len(risks) == 0


class TestAnimalFalsePositiveLowConfidence:
    """Low-confidence animal observations below min threshold
    should not produce risk events."""

    def test_animal_false_positive_low_confidence(
        self, policy_config: PolicyConfig,
    ) -> None:
        """Observations with confidence below min_detector_confidence (0.3)
        should produce no risks."""
        import uuid
        from packages.geo.helpers import point_along_bearing

        detector = AnimalConflictDetector(policy_config)
        road_lat, road_lon = 12.9716, 77.5946
        animal_lat, animal_lon = point_along_bearing(road_lat, road_lon, 270.0, 5.0)
        vehicle_lat, vehicle_lon = point_along_bearing(road_lat, road_lon, 180.0, 30.0)

        scenario = {
            "vehicles": [
                {
                    "actor_id": str(uuid.uuid4()),
                    "position": {"lat": vehicle_lat, "lon": vehicle_lon},
                    "position_uncertainty_m": 2.0,
                    "speed_mps": 15.0,
                    "heading_deg": 0.0,
                    "road_segment_id": "seg-1",
                },
            ],
            "dynamic_actors": [
                {
                    "track_id": str(uuid.uuid4()),
                    "actor_class": "cow",
                    "ts": datetime.now(timezone.utc),
                    "position": {"lat": animal_lat, "lon": animal_lon},
                    "speed_mps": 3.0,
                    "heading_deg": 90.0,
                    "detector_confidence": 0.1,  # Well below min_detector_confidence=0.3
                    "source_id": str(uuid.uuid4()),
                },
            ],
            "road_network": {
                "segments": [{"segment_id": "seg-1", "direction_deg": 0.0, "type": "URBAN"}],
            },
        }
        risks = detector.evaluate(scenario)
        assert len(risks) == 0
