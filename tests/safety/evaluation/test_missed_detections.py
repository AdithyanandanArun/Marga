"""Evaluation tests for missed detections.

Tests ensure that detectors reliably detect genuine risk scenarios
and do not miss obvious threats.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from packages.safety_policies.config import PolicyConfig
from services.safety_detectors.detectors.wrong_way import WrongWayDetector
from services.safety_detectors.detectors.emergency_braking import EmergencyBrakingDetector
from services.safety_detectors.detectors.animal_conflict import AnimalConflictDetector

from tests.safety.conftest import make_segment, make_vehicle_state
from tests.safety.fixtures.scenarios import (
    create_wrong_way_scenario,
    create_braking_scenario,
    create_animal_crossing_scenario,
)


class TestWrongWayMissedDetection:
    """Wrong-way detector must catch obvious wrong-way scenarios."""

    def test_wrong_way_missed_detection(self, policy_config: PolicyConfig) -> None:
        """A vehicle heading exactly opposite the road direction for many
        updates must always be detected."""
        detector = WrongWayDetector(policy_config)
        missed = 0
        n_trials = 20

        for _ in range(n_trials):
            scenario = create_wrong_way_scenario(
                road_direction_deg=0.0,
                vehicle_heading_deg=180.0,
                speed_mps=15.0,
                num_updates=6,
            )
            risks = []
            for vehicles in scenario["vehicles_sequence"]:
                ws = {
                    "vehicles": vehicles,
                    "road_network": scenario["road_network"],
                }
                risks.extend(detector.evaluate(ws))
            if not risks:
                missed += 1
            detector._persistence.clear()

        assert missed == 0, f"Missed {missed}/{n_trials} obvious wrong-way scenarios"


class TestBrakingMissedDetection:
    """Emergency braking detector must catch obvious hard braking."""

    def test_braking_missed_detection(self, policy_config: PolicyConfig) -> None:
        """A vehicle with -8 m/s^2 deceleration for sustained period
        must always be detected."""
        detector = EmergencyBrakingDetector(policy_config)
        scenario = create_braking_scenario(
            initial_speed_mps=25.0,
            deceleration_mps2=-8.0,
        )
        all_risks = []
        for idx, vehicles in enumerate(scenario["vehicles_sequence"]):
            ws = {
                "vehicles": vehicles,
                "road_network": scenario["road_network"],
            }
            risks = detector.evaluate(ws)
            if idx == 0:
                # Backdate braking start to simulate time passing
                for state in detector._braking_state.values():
                    state["braking_since"] = datetime.now(timezone.utc) - timedelta(seconds=1.0)
            all_risks.extend(risks)

        assert len(all_risks) > 0, "Failed to detect obvious hard braking"


class TestAnimalMissedDetection:
    """Animal detector must catch an animal heading directly into the road."""

    def test_animal_missed_detection(self, policy_config: PolicyConfig) -> None:
        """A high-confidence animal heading directly toward the road near
        a vehicle must always be detected."""
        missed = 0
        n_trials = 20

        for _ in range(n_trials):
            detector = AnimalConflictDetector(policy_config)
            scenario = create_animal_crossing_scenario(
                animal_class="cow",
                animal_speed=3.0,
                road_distance_m=10.0,
                heading_toward_road=True,
            )
            risks = detector.evaluate(scenario)
            if not risks:
                missed += 1

        assert missed == 0, f"Missed {missed}/{n_trials} obvious animal crossing scenarios"
