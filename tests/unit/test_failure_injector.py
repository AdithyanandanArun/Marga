"""
Unit tests for FailureInjector.

Tests verify that the injector correctly activates/deactivates failures based
on sim time and applies their effects through canonical interfaces.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Make the service package importable without installing it.
sys.path.insert(0, str(Path(__file__).parents[2] / "services" / "scenario-service"))

from app.failure_injector import FailureInjector
from app.schemas import (
    FailureScheduleEntry,
    FailureType,
    PositionEstimate,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_gps_entry(
    start: float,
    duration: float | None,
    uncertainty_m: float = 50.0,
    affected_actors: list[str] | None = None,
) -> FailureScheduleEntry:
    return FailureScheduleEntry(
        failure_type=FailureType.gps_degradation,
        start_sim_time_s=start,
        duration_s=duration,
        parameters={
            "uncertainty_m": uncertainty_m,
            "affected_actors": affected_actors or ["all"],
        },
    )


def _make_conn_entry(
    start: float,
    duration: float | None,
    services: list[str] | None = None,
) -> FailureScheduleEntry:
    return FailureScheduleEntry(
        failure_type=FailureType.connectivity_loss,
        start_sim_time_s=start,
        duration_s=duration,
        parameters={"affected_services": services or ["world_state"]},
    )


def _make_rsu_entry(
    rsu_id: str,
    start: float,
    duration: float | None,
) -> FailureScheduleEntry:
    return FailureScheduleEntry(
        failure_type=FailureType.rsu_failure,
        start_sim_time_s=start,
        duration_s=duration,
        parameters={"rsu_id": rsu_id},
    )


def _make_road_closure_entry(
    edge_id: str,
    start: float,
    duration: float | None,
) -> FailureScheduleEntry:
    return FailureScheduleEntry(
        failure_type=FailureType.road_closure,
        start_sim_time_s=start,
        duration_s=duration,
        parameters={"edge_id": edge_id},
    )


def _default_position(actor_id: str = "veh1") -> PositionEstimate:
    return PositionEstimate(
        actor_id=actor_id,
        latitude=12.9716,
        longitude=77.5946,
        uncertainty_m=5.0,
        confidence=1.0,
    )


# ---------------------------------------------------------------------------
# Tests: get_active_failures timing
# ---------------------------------------------------------------------------

class TestGetActiveFailures:
    injector = FailureInjector()

    def test_failure_not_active_before_start(self):
        """At t=0 a failure scheduled for t=60 must NOT be active."""
        schedule = [_make_gps_entry(start=60.0, duration=30.0)]
        effects = self.injector.get_active_failures(schedule, sim_time_s=0.0)
        assert effects == []

    def test_failure_active_at_exact_start(self):
        """At t=60 a failure starting at t=60 must be active."""
        schedule = [_make_gps_entry(start=60.0, duration=30.0)]
        effects = self.injector.get_active_failures(schedule, sim_time_s=60.0)
        assert len(effects) == 1
        assert effects[0].failure_type == FailureType.gps_degradation

    def test_failure_active_mid_window(self):
        """At t=75 (mid-window) with start=60, duration=30 the failure IS active."""
        schedule = [_make_gps_entry(start=60.0, duration=30.0)]
        effects = self.injector.get_active_failures(schedule, sim_time_s=75.0)
        assert len(effects) == 1

    def test_failure_expired_after_duration(self):
        """At t=91 with start=60, duration=30 (ends at t=90) the failure is NOT active."""
        schedule = [_make_gps_entry(start=60.0, duration=30.0)]
        effects = self.injector.get_active_failures(schedule, sim_time_s=91.0)
        assert effects == []

    def test_failure_active_at_exact_end(self):
        """At t=90 (exactly end) with start=60, duration=30 the failure is NOT active."""
        schedule = [_make_gps_entry(start=60.0, duration=30.0)]
        effects = self.injector.get_active_failures(schedule, sim_time_s=90.0)
        assert effects == []

    def test_failure_with_no_duration_persists(self):
        """A failure with duration_s=None should remain active indefinitely."""
        schedule = [_make_gps_entry(start=10.0, duration=None)]
        for t in (10.0, 100.0, 1000.0, 99999.0):
            effects = self.injector.get_active_failures(schedule, sim_time_s=t)
            assert len(effects) == 1, f"Expected active at t={t}"

    def test_multiple_failures_mixed_activity(self):
        """Two failures with non-overlapping windows are independently tracked."""
        schedule = [
            _make_gps_entry(start=0.0, duration=50.0),
            _make_conn_entry(start=60.0, duration=30.0),
        ]
        effects_at_25 = self.injector.get_active_failures(schedule, sim_time_s=25.0)
        assert len(effects_at_25) == 1
        assert effects_at_25[0].failure_type == FailureType.gps_degradation

        effects_at_70 = self.injector.get_active_failures(schedule, sim_time_s=70.0)
        assert len(effects_at_70) == 1
        assert effects_at_70[0].failure_type == FailureType.connectivity_loss

    def test_empty_schedule(self):
        effects = self.injector.get_active_failures([], sim_time_s=100.0)
        assert effects == []


# ---------------------------------------------------------------------------
# Tests: GPS degradation
# ---------------------------------------------------------------------------

class TestApplyGpsDegradation:
    injector = FailureInjector()

    def test_gps_degradation_increases_uncertainty(self):
        """GPS degradation must increase uncertainty_m on the position estimate."""
        schedule = [_make_gps_entry(start=0.0, duration=None, uncertainty_m=60.0)]
        effects = self.injector.get_active_failures(schedule, sim_time_s=10.0)
        position = _default_position()
        result = self.injector.apply_gps_degradation(position, effects)
        assert result.uncertainty_m == 60.0
        assert result.uncertainty_m > position.uncertainty_m

    def test_gps_degradation_decreases_confidence(self):
        """GPS degradation must reduce confidence on the position estimate."""
        schedule = [_make_gps_entry(start=0.0, duration=None, uncertainty_m=80.0)]
        effects = self.injector.get_active_failures(schedule, sim_time_s=5.0)
        position = _default_position()
        result = self.injector.apply_gps_degradation(position, effects)
        assert result.confidence < position.confidence
        assert result.confidence >= 0.0

    def test_gps_degradation_worst_case_wins(self):
        """When multiple GPS effects are active, the highest uncertainty wins."""
        schedule = [
            _make_gps_entry(start=0.0, duration=None, uncertainty_m=30.0),
            _make_gps_entry(start=0.0, duration=None, uncertainty_m=90.0),
        ]
        effects = self.injector.get_active_failures(schedule, sim_time_s=5.0)
        position = _default_position()
        result = self.injector.apply_gps_degradation(position, effects)
        assert result.uncertainty_m == 90.0

    def test_gps_degradation_does_not_affect_unrelated_actors(self):
        """When affected_actors excludes an actor, its position is unchanged."""
        entry = FailureScheduleEntry(
            failure_type=FailureType.gps_degradation,
            start_sim_time_s=0.0,
            duration_s=None,
            parameters={"uncertainty_m": 80.0, "affected_actors": ["veh2"]},
        )
        effects = self.injector.get_active_failures([entry], sim_time_s=5.0)
        position = _default_position(actor_id="veh1")
        result = self.injector.apply_gps_degradation(position, effects, actor_id="veh1")
        assert result.uncertainty_m == position.uncertainty_m
        assert result.confidence == position.confidence

    def test_gps_degradation_affects_specific_actor(self):
        """When affected_actors lists a specific actor ID, it IS affected."""
        entry = FailureScheduleEntry(
            failure_type=FailureType.gps_degradation,
            start_sim_time_s=0.0,
            duration_s=None,
            parameters={"uncertainty_m": 55.0, "affected_actors": ["veh3"]},
        )
        effects = self.injector.get_active_failures([entry], sim_time_s=5.0)
        position = _default_position(actor_id="veh3")
        result = self.injector.apply_gps_degradation(position, effects, actor_id="veh3")
        assert result.uncertainty_m == 55.0

    def test_no_gps_effects_returns_unchanged_position(self):
        """When no GPS failures are active, the position is returned unchanged."""
        position = _default_position()
        result = self.injector.apply_gps_degradation(position, [])
        assert result.uncertainty_m == position.uncertainty_m
        assert result.confidence == position.confidence


# ---------------------------------------------------------------------------
# Tests: connectivity loss
# ---------------------------------------------------------------------------

class TestGetOfflineServices:
    injector = FailureInjector()

    def test_connectivity_loss_returns_correct_services(self):
        schedule = [_make_conn_entry(start=0.0, duration=None, services=["world_state", "alerts"])]
        effects = self.injector.get_active_failures(schedule, sim_time_s=5.0)
        offline = self.injector.get_offline_services(effects)
        assert "world_state" in offline
        assert "alerts" in offline

    def test_connectivity_loss_accumulates_across_entries(self):
        schedule = [
            _make_conn_entry(start=0.0, duration=None, services=["world_state"]),
            _make_conn_entry(start=0.0, duration=None, services=["alerts"]),
        ]
        effects = self.injector.get_active_failures(schedule, sim_time_s=5.0)
        offline = self.injector.get_offline_services(effects)
        assert offline == {"world_state", "alerts"}

    def test_no_connectivity_failures_returns_empty_set(self):
        offline = self.injector.get_offline_services([])
        assert offline == set()

    def test_service_not_offline_without_matching_failure(self):
        schedule = [_make_conn_entry(start=0.0, duration=None, services=["alerts"])]
        effects = self.injector.get_active_failures(schedule, sim_time_s=5.0)
        offline = self.injector.get_offline_services(effects)
        assert "world_state" not in offline


# ---------------------------------------------------------------------------
# Tests: RSU failure
# ---------------------------------------------------------------------------

class TestIsRsuOperational:
    injector = FailureInjector()

    def test_rsu_marked_offline_when_failed(self):
        schedule = [_make_rsu_entry("rsu_123", start=0.0, duration=None)]
        effects = self.injector.get_active_failures(schedule, sim_time_s=10.0)
        assert self.injector.is_rsu_operational("rsu_123", effects) is False

    def test_other_rsu_unaffected_by_different_failure(self):
        schedule = [_make_rsu_entry("rsu_123", start=0.0, duration=None)]
        effects = self.injector.get_active_failures(schedule, sim_time_s=10.0)
        assert self.injector.is_rsu_operational("rsu_456", effects) is True

    def test_rsu_operational_when_no_failures_active(self):
        schedule = [_make_rsu_entry("rsu_123", start=60.0, duration=30.0)]
        effects = self.injector.get_active_failures(schedule, sim_time_s=0.0)
        assert self.injector.is_rsu_operational("rsu_123", effects) is True

    def test_rsu_recovers_after_duration_expires(self):
        schedule = [_make_rsu_entry("rsu_123", start=0.0, duration=30.0)]
        effects = self.injector.get_active_failures(schedule, sim_time_s=31.0)
        assert self.injector.is_rsu_operational("rsu_123", effects) is True


# ---------------------------------------------------------------------------
# Tests: road closure
# ---------------------------------------------------------------------------

class TestRoadEvents:
    injector = FailureInjector()

    def test_road_closure_returns_correct_event(self):
        schedule = [_make_road_closure_entry("edge_abc", start=0.0, duration=None)]
        effects = self.injector.get_active_failures(schedule, sim_time_s=5.0)
        events = self.injector.get_road_events_from_failures(effects, sim_time_s=5.0)
        assert len(events) == 1
        assert events[0]["edge_id"] == "edge_abc"
        assert events[0]["event_type"] == "close"

    def test_road_narrowing_returns_correct_event(self):
        entry = FailureScheduleEntry(
            failure_type=FailureType.road_narrowing,
            start_sim_time_s=0.0,
            duration_s=None,
            parameters={"edge_id": "edge_xyz", "lanes_remaining": 1},
        )
        effects = self.injector.get_active_failures([entry], sim_time_s=5.0)
        events = self.injector.get_road_events_from_failures(effects, sim_time_s=5.0)
        assert len(events) == 1
        assert events[0]["edge_id"] == "edge_xyz"
        assert events[0]["event_type"] == "narrow"
        assert events[0]["value"] == 1

    def test_no_road_events_when_no_failures(self):
        events = self.injector.get_road_events_from_failures([], sim_time_s=0.0)
        assert events == []
