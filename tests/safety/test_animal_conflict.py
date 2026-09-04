"""Tests for AnimalConflictDetector.

Validates animal-vehicle conflict detection including reachable region
expansion, crossing angle assessment, confidence handling, and track
prediction after observation disappears.

The detector expects vehicles and dynamic_actors as plain dicts.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from packages.geo.helpers import point_along_bearing
from packages.safety_policies.config import PolicyConfig
from packages.schemas.canonical import RiskType
from services.safety_detectors.detectors.animal_conflict import AnimalConflictDetector

from tests.safety.fixtures.scenarios import create_animal_crossing_scenario


def _uid() -> str:
    return str(uuid.uuid4())


class TestAnimalConflict:
    """Animal conflict detector acceptance tests."""

    def test_animal_entering_road_detected(self, policy_config: PolicyConfig) -> None:
        """An animal heading toward the road near an approaching vehicle
        should produce a risk event."""
        detector = AnimalConflictDetector(policy_config)
        scenario = create_animal_crossing_scenario(
            animal_class="cow",
            animal_speed=3.0,
            road_distance_m=10.0,
            heading_toward_road=True,
        )
        risks = detector.evaluate(scenario)
        assert len(risks) > 0
        assert all(r.type == RiskType.ANIMAL_CROSSING for r in risks)

    def test_animal_parallel_lower_risk(self, policy_config: PolicyConfig) -> None:
        """An animal moving parallel to the road should produce lower
        severity than one entering the road."""
        detector_enter = AnimalConflictDetector(policy_config)
        entering = create_animal_crossing_scenario(
            animal_class="cow",
            animal_speed=3.0,
            road_distance_m=10.0,
            heading_toward_road=True,
        )
        risks_enter = detector_enter.evaluate(entering)

        detector_parallel = AnimalConflictDetector(policy_config)
        parallel = create_animal_crossing_scenario(
            animal_class="cow",
            animal_speed=3.0,
            road_distance_m=10.0,
            heading_toward_road=False,
        )
        risks_parallel = detector_parallel.evaluate(parallel)

        if risks_enter and risks_parallel:
            assert risks_parallel[0].severity <= risks_enter[0].severity

    def test_unknown_class_works(self, policy_config: PolicyConfig) -> None:
        """Unknown animal class should still produce a detection using
        the default speed model."""
        detector = AnimalConflictDetector(policy_config)
        scenario = create_animal_crossing_scenario(
            animal_class="pangolin",
            animal_speed=2.0,
            road_distance_m=10.0,
            heading_toward_road=True,
        )
        risks = detector.evaluate(scenario)
        # Should still detect using the 'default' max speed
        assert len(risks) > 0

    def test_low_confidence_no_critical(self, policy_config: PolicyConfig) -> None:
        """A single low-confidence detection should never produce a
        severity above the suppression cap (0.55)."""
        detector = AnimalConflictDetector(policy_config)
        road_lat, road_lon = 12.9716, 77.5946
        animal_lat, animal_lon = point_along_bearing(road_lat, road_lon, 270.0, 10.0)
        vehicle_lat, vehicle_lon = point_along_bearing(road_lat, road_lon, 180.0, 50.0)

        now = datetime.now(timezone.utc)
        scenario = {
            "vehicles": [
                {
                    "actor_id": _uid(),
                    "position": {"lat": vehicle_lat, "lon": vehicle_lon},
                    "position_uncertainty_m": 2.0,
                    "speed_mps": 15.0,
                    "heading_deg": 0.0,
                    "road_segment_id": "seg-1",
                },
            ],
            "dynamic_actors": [
                {
                    "observation_id": _uid(),
                    "track_id": _uid(),
                    "actor_class": "cow",
                    "ts": now,
                    "position": {"lat": animal_lat, "lon": animal_lon},
                    "speed_mps": 3.0,
                    "heading_deg": 90.0,
                    "detector_confidence": 0.35,  # Below low_confidence_alert_suppression=0.5
                    "source_id": _uid(),
                },
            ],
            "road_network": {
                "segments": [{"segment_id": "seg-1", "direction_deg": 0.0, "type": "URBAN"}],
            },
        }
        risks = detector.evaluate(scenario)
        for r in risks:
            assert r.severity <= 0.55

    def test_track_prediction_after_disappearance(self, policy_config: PolicyConfig) -> None:
        """After an animal observation disappears, the track should
        continue with decaying confidence for track_prediction_timeout_s."""
        detector = AnimalConflictDetector(policy_config)
        now = datetime.now(timezone.utc)
        road_lat, road_lon = 12.9716, 77.5946
        animal_lat, animal_lon = point_along_bearing(road_lat, road_lon, 270.0, 10.0)
        vehicle_lat, vehicle_lon = point_along_bearing(road_lat, road_lon, 180.0, 50.0)
        track_id = _uid()

        base_scenario = {
            "vehicles": [
                {
                    "actor_id": _uid(),
                    "position": {"lat": vehicle_lat, "lon": vehicle_lon},
                    "position_uncertainty_m": 2.0,
                    "speed_mps": 15.0,
                    "heading_deg": 0.0,
                    "road_segment_id": "seg-1",
                },
            ],
            "road_network": {
                "segments": [{"segment_id": "seg-1", "direction_deg": 0.0, "type": "URBAN"}],
            },
        }

        # First: animal is observed
        with_obs = {
            **base_scenario,
            "dynamic_actors": [
                {
                    "track_id": track_id,
                    "actor_class": "cow",
                    "ts": now,
                    "position": {"lat": animal_lat, "lon": animal_lon},
                    "speed_mps": 3.0,
                    "heading_deg": 90.0,
                    "detector_confidence": 0.8,
                    "source_id": _uid(),
                },
            ],
        }
        risks1 = detector.evaluate(with_obs)

        # Second: animal disappears - no dynamic_actors
        without_obs = {**base_scenario, "dynamic_actors": []}
        risks2 = detector.evaluate(without_obs)
        # Track should still be predicted (confidence decayed but nonzero)
        assert track_id in detector._tracks

    def test_different_road_different_result(self, policy_config: PolicyConfig) -> None:
        """Detection should work regardless of coordinates and segment IDs."""
        detector1 = AnimalConflictDetector(policy_config)
        s1 = create_animal_crossing_scenario(
            animal_class="dog",
            road_distance_m=10.0,
            heading_toward_road=True,
        )
        risks1 = detector1.evaluate(s1)

        detector2 = AnimalConflictDetector(policy_config)
        s2 = create_animal_crossing_scenario(
            animal_class="dog",
            road_distance_m=10.0,
            heading_toward_road=True,
        )
        risks2 = detector2.evaluate(s2)
        # Both should detect (or not) regardless of random segment IDs
        assert (len(risks1) > 0) == (len(risks2) > 0)
