"""
Unit tests for ScenarioDefinition serialization, validation, and fixture loading.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[2] / "services" / "scenario-service"))

from app.schemas import (
    EnvironmentConditions,
    FailureScheduleEntry,
    FailureType,
    ScenarioDefinition,
    TrafficComposition,
)

FIXTURES_DIR = (
    Path(__file__).parents[2] / "services" / "scenario-service" / "fixtures"
)


# ---------------------------------------------------------------------------
# Serialisation round-trip
# ---------------------------------------------------------------------------

class TestScenarioSerialisation:

    def _make_scenario(self, seed: int = 42, name: str = "Test") -> ScenarioDefinition:
        return ScenarioDefinition(
            name=name,
            osm_region="test_region",
            seed=seed,
            duration_s=120.0,
            failure_schedule=[
                FailureScheduleEntry(
                    failure_type=FailureType.gps_degradation,
                    start_sim_time_s=10.0,
                    duration_s=30.0,
                    parameters={"uncertainty_m": 40.0, "affected_actors": ["all"]},
                )
            ],
            tags=["test"],
        )

    def test_round_trip_json(self):
        """ScenarioDefinition must survive model_dump_json → model_validate_json."""
        original = self._make_scenario()
        json_str = original.model_dump_json()
        restored = ScenarioDefinition.model_validate_json(json_str)
        assert restored.scenario_id == original.scenario_id
        assert restored.name == original.name
        assert restored.seed == original.seed
        assert restored.duration_s == original.duration_s
        assert len(restored.failure_schedule) == len(original.failure_schedule)

    def test_round_trip_dict(self):
        """ScenarioDefinition must survive model_dump → model_validate."""
        original = self._make_scenario()
        d = original.model_dump()
        restored = ScenarioDefinition.model_validate(d)
        assert restored.scenario_id == original.scenario_id

    def test_failure_schedule_entry_ids_preserved(self):
        """Entry IDs must survive the round-trip."""
        original = self._make_scenario()
        orig_id = original.failure_schedule[0].entry_id
        json_str = original.model_dump_json()
        restored = ScenarioDefinition.model_validate_json(json_str)
        assert restored.failure_schedule[0].entry_id == orig_id

    def test_nested_models_preserved(self):
        """TrafficComposition and EnvironmentConditions must survive round-trip."""
        original = ScenarioDefinition(
            name="Nested",
            osm_region="test",
            seed=1,
            traffic_composition=TrafficComposition(
                car_fraction=0.3, motorcycle_fraction=0.4, auto_rickshaw_fraction=0.2
            ),
            environment=EnvironmentConditions(visibility_m=200.0, precipitation="fog"),
        )
        restored = ScenarioDefinition.model_validate_json(original.model_dump_json())
        assert restored.traffic_composition.car_fraction == pytest.approx(0.3)
        assert restored.environment.precipitation == "fog"
        assert restored.environment.visibility_m == pytest.approx(200.0)


# ---------------------------------------------------------------------------
# Seed isolation
# ---------------------------------------------------------------------------

class TestSeedIsolation:

    def test_different_seeds_produce_different_scenario_ids(self):
        """
        Two ScenarioDefinitions with different seeds must have different
        scenario_ids (auto-generated UUIDs are random, not seed-derived,
        but the test validates that the system does not accidentally share IDs).
        """
        scenarios = [
            ScenarioDefinition(name="S", osm_region="r", seed=i)
            for i in range(10)
        ]
        ids = [s.scenario_id for s in scenarios]
        assert len(set(ids)) == len(ids), "All scenario_ids must be unique"

    def test_same_definition_twice_gets_different_ids(self):
        """
        Two independently created ScenarioDefinitions with identical fields
        (except scenario_id) must have different auto-generated IDs.
        """
        s1 = ScenarioDefinition(name="S", osm_region="r", seed=99)
        s2 = ScenarioDefinition(name="S", osm_region="r", seed=99)
        assert s1.scenario_id != s2.scenario_id


# ---------------------------------------------------------------------------
# TrafficComposition validation
# ---------------------------------------------------------------------------

class TestTrafficCompositionValidation:

    def test_valid_fractions_accepted(self):
        tc = TrafficComposition(
            car_fraction=0.5,
            truck_fraction=0.1,
            motorcycle_fraction=0.3,
            auto_rickshaw_fraction=0.1,
        )
        assert tc.car_fraction == pytest.approx(0.5)

    def test_fraction_below_zero_raises(self):
        with pytest.raises(Exception):
            TrafficComposition(car_fraction=-0.1)

    def test_fraction_above_one_raises(self):
        with pytest.raises(Exception):
            TrafficComposition(motorcycle_fraction=1.1)

    def test_pedestrian_density_zero_valid(self):
        tc = TrafficComposition(pedestrian_density=0.0)
        assert tc.pedestrian_density == pytest.approx(0.0)

    def test_pedestrian_density_one_valid(self):
        tc = TrafficComposition(pedestrian_density=1.0)
        assert tc.pedestrian_density == pytest.approx(1.0)

    def test_pedestrian_density_above_one_raises(self):
        with pytest.raises(Exception):
            TrafficComposition(pedestrian_density=1.01)


# ---------------------------------------------------------------------------
# Fixture loading
# ---------------------------------------------------------------------------

class TestFixtureLoading:

    @pytest.mark.parametrize("fixture_name", [
        "bangalore_morning_rush.json",
        "highway_fog_incident.json",
    ])
    def test_fixture_parses_as_scenario_definition(self, fixture_name: str):
        """Each fixture JSON file must parse into a valid ScenarioDefinition."""
        path = FIXTURES_DIR / fixture_name
        assert path.exists(), f"Fixture not found: {path}"
        raw = path.read_text(encoding="utf-8")
        scenario = ScenarioDefinition.model_validate_json(raw)
        assert scenario.scenario_id
        assert scenario.name
        assert scenario.osm_region
        assert scenario.seed != 0

    def test_bangalore_fixture_has_expected_tags(self):
        path = FIXTURES_DIR / "bangalore_morning_rush.json"
        scenario = ScenarioDefinition.model_validate_json(
            path.read_text(encoding="utf-8")
        )
        assert "bangalore" in scenario.tags

    def test_bangalore_fixture_has_failure_schedule(self):
        path = FIXTURES_DIR / "bangalore_morning_rush.json"
        scenario = ScenarioDefinition.model_validate_json(
            path.read_text(encoding="utf-8")
        )
        assert len(scenario.failure_schedule) > 0

    def test_highway_fixture_has_fog_condition(self):
        path = FIXTURES_DIR / "highway_fog_incident.json"
        scenario = ScenarioDefinition.model_validate_json(
            path.read_text(encoding="utf-8")
        )
        assert scenario.environment.precipitation == "fog"
        assert scenario.environment.visibility_m < 200.0

    def test_highway_fixture_has_wrong_way_vehicle_in_schedule(self):
        path = FIXTURES_DIR / "highway_fog_incident.json"
        scenario = ScenarioDefinition.model_validate_json(
            path.read_text(encoding="utf-8")
        )
        types = [e.failure_type for e in scenario.failure_schedule]
        assert FailureType.wrong_way_vehicle in types

    def test_fixture_round_trips_through_dict(self):
        """Fixture → ScenarioDefinition → dict → ScenarioDefinition must be stable."""
        path = FIXTURES_DIR / "bangalore_morning_rush.json"
        scenario = ScenarioDefinition.model_validate_json(
            path.read_text(encoding="utf-8")
        )
        d = scenario.model_dump()
        restored = ScenarioDefinition.model_validate(d)
        assert restored.scenario_id == scenario.scenario_id
        assert len(restored.failure_schedule) == len(scenario.failure_schedule)

    def test_import_from_fixture_dict(self):
        """The fixture JSON must be importable via model_validate(dict)."""
        path = FIXTURES_DIR / "highway_fog_incident.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        scenario = ScenarioDefinition.model_validate(data)
        assert scenario.scenario_id == data["scenario_id"]
