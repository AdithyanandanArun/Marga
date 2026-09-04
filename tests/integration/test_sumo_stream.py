"""
Integration-style tests for the simulation adapter stream.

All tests run WITHOUT requiring SUMO to be installed.
They exercise the normalizer, runner, and factory using a MockAdapter
that implements the SimulationAdapter Protocol with hard-coded data.
"""

from __future__ import annotations

import pytest
from datetime import datetime, timezone
from typing import Iterable, Union

from services.simulation_adapter.normalizer import SumoNormalizer
from services.simulation_adapter.schemas import (
    CanonicalEvent,
    DynamicActorObservation,
    InfrastructureState,
    PedestrianState,
    Position,
    PositionEstimate,
    RoadState,
    SignalPhase,
    VehicleState,
    VehicleType,
)
from services.simulation_adapter.runner import SimulationRunner
from services.simulation_adapter.factory import create_adapter


# ---------------------------------------------------------------------------
# Mock adapter implementing SimulationAdapter Protocol
# ---------------------------------------------------------------------------

class MockAdapter:
    """
    In-memory SimulationAdapter that returns hard-coded canonical objects.
    No SUMO installation required.
    """

    SOURCE = "sumo_traci"  # simulate traci source by default

    def __init__(self, source: str = "sumo_traci") -> None:
        self.SOURCE = source
        self._scenario_run_id: str = ""
        self._current_time: float = 0.0
        self._started = False
        self._step_count = 0

    def start(self, config: dict) -> None:
        self._started = True

    def stop(self) -> None:
        self._started = False

    def step(self, dt: float) -> None:
        self._current_time += dt
        self._step_count += 1

    def list_actors(self) -> Iterable[Union[VehicleState, PedestrianState, DynamicActorObservation]]:
        now = datetime.now(timezone.utc)
        yield VehicleState(
            vehicle_id="veh_001",
            timestamp_utc=now,
            position=PositionEstimate(lat=12.9716, lon=77.5946, source=self.SOURCE),
            speed_mps=13.89,
            heading_deg=90.0,
            vehicle_type=VehicleType.car,
            source=self.SOURCE,
            scenario_run_id=self._scenario_run_id,
        )
        yield PedestrianState(
            pedestrian_id="ped_001",
            timestamp_utc=now,
            position=PositionEstimate(lat=12.9718, lon=77.5948, source=self.SOURCE),
            speed_mps=1.2,
            heading_deg=0.0,
            source=self.SOURCE,
            scenario_run_id=self._scenario_run_id,
        )

    def get_signal_states(self) -> list[InfrastructureState]:
        now = datetime.now(timezone.utc)
        return [
            InfrastructureState(
                infrastructure_id="tls_001",
                timestamp_utc=now,
                position=Position(lat=12.9717, lon=77.5947),
                signal_phase=SignalPhase.green,
                phase_remaining_s=25.0,
                source=self.SOURCE,
                scenario_run_id=self._scenario_run_id,
            )
        ]

    def get_road_states(self) -> list[RoadState]:
        return []

    def apply_vehicle_command(self, command: dict) -> None:
        pass

    def apply_signal_command(self, command: dict) -> None:
        pass

    def apply_road_event(self, event: dict) -> None:
        pass

    def reset(self, scenario_run_id: str) -> None:
        self._scenario_run_id = scenario_run_id
        self._current_time = 0.0
        self._step_count = 0

    @property
    def current_time(self) -> float:
        return self._current_time

    @property
    def scenario_run_id(self) -> str:
        return self._scenario_run_id


# ---------------------------------------------------------------------------
# Heading normalisation tests
# ---------------------------------------------------------------------------

class TestHeadingNormalisation:
    """Test SumoNormalizer.normalize_heading converts SUMO angles correctly."""

    def test_sumo_east_to_canonical_east(self):
        """SUMO 0° = East → canonical 90° (East, clockwise from North)."""
        assert SumoNormalizer.normalize_heading(0.0) == pytest.approx(90.0)

    def test_sumo_north_to_canonical_north(self):
        """SUMO 90° = North → canonical 0° (North)."""
        assert SumoNormalizer.normalize_heading(90.0) == pytest.approx(0.0)

    def test_sumo_west_to_canonical_west(self):
        """SUMO 180° = West → canonical 270°."""
        assert SumoNormalizer.normalize_heading(180.0) == pytest.approx(270.0)

    def test_sumo_south_to_canonical_south(self):
        """SUMO 270° = South → canonical 180°."""
        assert SumoNormalizer.normalize_heading(270.0) == pytest.approx(180.0)

    def test_heading_stays_in_0_360_range(self):
        """Canonical heading must always be in [0, 360)."""
        for sumo_angle in range(-360, 720, 15):
            result = SumoNormalizer.normalize_heading(float(sumo_angle))
            assert 0.0 <= result < 360.0, f"Out of range for SUMO angle {sumo_angle}: {result}"


# ---------------------------------------------------------------------------
# VehicleState normalisation tests
# ---------------------------------------------------------------------------

class TestVehicleStateNormalisation:
    """Test that normalizer converts raw SUMO vehicle data to canonical types."""

    def setup_method(self):
        self.normalizer = SumoNormalizer(
            origin_lat=12.9716,
            origin_lon=77.5946,
        )
        self.now = datetime.now(timezone.utc)

    def test_speed_passes_through_unchanged(self):
        """SUMO speed is already in m/s — must not be converted."""
        raw = {"x": 0.0, "y": 0.0, "speed": 13.89, "angle": 90.0, "type_id": "car"}
        state = self.normalizer.normalize_vehicle_state(
            "v1", raw, self.now, "run1", "sumo_traci"
        )
        assert state.speed_mps == pytest.approx(13.89)

    def test_heading_is_normalised(self):
        """Heading must be converted from SUMO CCW-from-East to CW-from-North."""
        raw = {"x": 0.0, "y": 0.0, "speed": 0.0, "angle": 0.0, "type_id": "car"}
        state = self.normalizer.normalize_vehicle_state(
            "v1", raw, self.now, "run1", "sumo_traci"
        )
        assert state.heading_deg == pytest.approx(90.0)

    def test_vehicle_type_mapped_correctly(self):
        """SUMO type 'bus' should map to VehicleType.bus."""
        raw = {"x": 0.0, "y": 0.0, "speed": 0.0, "angle": 0.0, "type_id": "bus"}
        state = self.normalizer.normalize_vehicle_state(
            "v1", raw, self.now, "run1", "sumo_traci"
        )
        assert state.vehicle_type == VehicleType.bus

    def test_unknown_type_defaults_to_car(self):
        """Unknown SUMO type IDs should default to VehicleType.car."""
        raw = {"x": 0.0, "y": 0.0, "speed": 5.0, "angle": 45.0, "type_id": "exotic_future_vehicle"}
        state = self.normalizer.normalize_vehicle_state(
            "v1", raw, self.now, "run1", "sumo_traci"
        )
        assert state.vehicle_type == VehicleType.car

    def test_acceleration_optional(self):
        """Acceleration is optional and should pass through when provided."""
        raw = {"x": 0.0, "y": 0.0, "speed": 10.0, "angle": 0.0, "type_id": "car", "acceleration": 2.5}
        state = self.normalizer.normalize_vehicle_state(
            "v1", raw, self.now, "run1", "sumo_traci"
        )
        assert state.acceleration_mps2 == pytest.approx(2.5)

    def test_source_field_preserved(self):
        """The source tag in VehicleState must match the argument passed."""
        raw = {"x": 0.0, "y": 0.0, "speed": 0.0, "angle": 0.0, "type_id": "car"}
        state = self.normalizer.normalize_vehicle_state(
            "v1", raw, self.now, "run1", "sumo_libsumo"
        )
        assert state.source == "sumo_libsumo"

    def test_schema_version_is_set(self):
        raw = {"x": 0.0, "y": 0.0, "speed": 0.0, "angle": 0.0, "type_id": "car"}
        state = self.normalizer.normalize_vehicle_state(
            "v1", raw, self.now, "run1", "sumo_traci"
        )
        assert state.schema_version == "1.0"

    def test_trace_id_auto_generated(self):
        """trace_id must be auto-generated as a non-empty UUID string."""
        raw = {"x": 0.0, "y": 0.0, "speed": 0.0, "angle": 0.0, "type_id": "car"}
        state = self.normalizer.normalize_vehicle_state(
            "v1", raw, self.now, "run1", "sumo_traci"
        )
        assert len(state.trace_id) == 36  # UUID4 format


# ---------------------------------------------------------------------------
# Type map tests
# ---------------------------------------------------------------------------

class TestVehicleTypeMapping:
    """Test that SUMO type IDs are correctly mapped to canonical VehicleType."""

    def setup_method(self):
        self.normalizer = SumoNormalizer()

    @pytest.mark.parametrize("sumo_type,expected", [
        ("car", VehicleType.car),
        ("passenger", VehicleType.car),
        ("DEFAULT_VEHTYPE", VehicleType.car),
        ("truck", VehicleType.truck),
        ("trailer", VehicleType.truck),
        ("bus", VehicleType.bus),
        ("coach", VehicleType.bus),
        ("motorcycle", VehicleType.motorcycle),
        ("moped", VehicleType.motorcycle),
        ("auto_rickshaw", VehicleType.auto_rickshaw),
        ("auto", VehicleType.auto_rickshaw),
        ("tractor", VehicleType.tractor),
        ("bicycle", VehicleType.bicycle),
        ("bike", VehicleType.bicycle),
        ("emergency", VehicleType.emergency),
        ("ambulance", VehicleType.emergency),
        ("police", VehicleType.emergency),
    ])
    def test_type_mapping(self, sumo_type: str, expected: VehicleType):
        result = self.normalizer.map_vehicle_type(sumo_type)
        assert result == expected, f"Expected {expected} for type '{sumo_type}', got {result}"


# ---------------------------------------------------------------------------
# SimulationRunner tick tests
# ---------------------------------------------------------------------------

class TestSimulationRunnerTick:
    """Test that SimulationRunner._tick() produces correct CanonicalEvents."""

    def setup_method(self):
        self.events_received: list[list[CanonicalEvent]] = []
        self.adapter = MockAdapter()
        self.runner = SimulationRunner(
            adapter=self.adapter,
            world_state_callback=self.events_received.append,
            tick_hz=10.0,
        )

    def test_tick_returns_events(self):
        """_tick() must return a non-empty list of CanonicalEvent."""
        events = self.runner._tick()
        assert len(events) > 0

    def test_vehicle_event_type(self):
        """Vehicle states must produce 'actor.state.updated' events."""
        events = self.runner._tick()
        vehicle_events = [e for e in events if e.event_type == "actor.state.updated"]
        assert len(vehicle_events) >= 1

    def test_pedestrian_event_type(self):
        """Pedestrian states must produce 'actor.state.updated' events."""
        events = self.runner._tick()
        ped_events = [
            e for e in events
            if e.event_type == "actor.state.updated"
            and "pedestrian_id" in e.payload
        ]
        assert len(ped_events) == 1

    def test_signal_event_type(self):
        """Signal states must produce 'infrastructure.signal.updated' events."""
        events = self.runner._tick()
        signal_events = [e for e in events if e.event_type == "infrastructure.signal.updated"]
        assert len(signal_events) == 1

    def test_vehicle_payload_has_vehicle_id(self):
        """Vehicle CanonicalEvent payload must contain vehicle_id."""
        events = self.runner._tick()
        vehicle_events = [
            e for e in events
            if e.event_type == "actor.state.updated" and "vehicle_id" in e.payload
        ]
        assert any(e.payload["vehicle_id"] == "veh_001" for e in vehicle_events)

    def test_stats_incremented(self):
        """Runner stats must track tick and event counts."""
        self.runner._tick()
        self.runner._tick()
        assert self.runner.stats.tick_count == 2
        assert self.runner.stats.event_count > 0

    def test_events_have_event_id(self):
        """Every CanonicalEvent must have a non-empty event_id."""
        events = self.runner._tick()
        for event in events:
            assert event.event_id, f"Missing event_id on {event.event_type}"

    def test_events_have_trace_id(self):
        """Every CanonicalEvent must have a non-empty trace_id."""
        events = self.runner._tick()
        for event in events:
            assert event.trace_id, f"Missing trace_id on {event.event_type}"


# ---------------------------------------------------------------------------
# Backend equivalence test (traci vs libsumo produce same canonical output)
# ---------------------------------------------------------------------------

class TestBackendEquivalence:
    """
    Test that switching from traci to libsumo produces identical canonical output.

    We use two MockAdapters with different source tags to simulate the two
    backends. The canonical payload schema must be identical regardless of source.
    """

    def test_canonical_schema_identical_across_backends(self):
        """
        Canonical event payload keys must be the same for both backends.
        Only the 'source' field value differs.
        """
        collected_traci: list[CanonicalEvent] = []
        collected_libsumo: list[CanonicalEvent] = []

        runner_traci = SimulationRunner(
            adapter=MockAdapter(source="sumo_traci"),
            world_state_callback=lambda evts: collected_traci.extend(evts),
        )
        runner_libsumo = SimulationRunner(
            adapter=MockAdapter(source="sumo_libsumo"),
            world_state_callback=lambda evts: collected_libsumo.extend(evts),
        )

        runner_traci._tick()
        runner_libsumo._tick()

        assert len(collected_traci) == len(collected_libsumo), (
            "Both backends must produce the same number of events per tick"
        )

        for evt_traci, evt_libsumo in zip(collected_traci, collected_libsumo):
            assert evt_traci.event_type == evt_libsumo.event_type
            # Payload keys must be identical
            assert set(evt_traci.payload.keys()) == set(evt_libsumo.payload.keys()), (
                f"Payload key mismatch for event_type={evt_traci.event_type}\n"
                f"  traci keys:   {sorted(evt_traci.payload.keys())}\n"
                f"  libsumo keys: {sorted(evt_libsumo.payload.keys())}"
            )
            # Source field in payload differs (expected)
            assert evt_traci.payload.get("source") != evt_libsumo.payload.get("source"), (
                "Source should differ between traci and libsumo"
            )
            # All other fields must match
            for key in evt_traci.payload:
                if key == "source":
                    continue
                if key in ("trace_id", "timestamp_utc", "position"):
                    # These are unique per instance, not comparable
                    continue
                assert evt_traci.payload[key] == evt_libsumo.payload[key], (
                    f"Payload field '{key}' differs: {evt_traci.payload[key]} vs {evt_libsumo.payload[key]}"
                )


# ---------------------------------------------------------------------------
# Factory tests
# ---------------------------------------------------------------------------

class TestCreateAdapter:
    """Test that create_adapter raises ValueError for unknown backends."""

    def test_unknown_backend_raises_value_error(self):
        normalizer = SumoNormalizer()
        with pytest.raises(ValueError, match="Unknown simulation backend"):
            create_adapter("unknown_backend", normalizer)

    def test_unknown_backend_message_includes_name(self):
        normalizer = SumoNormalizer()
        with pytest.raises(ValueError, match="foobar"):
            create_adapter("foobar", normalizer)

    def test_traci_backend_returns_adapter_without_sumo(self):
        """create_adapter('traci') must succeed without SUMO installed."""
        normalizer = SumoNormalizer()
        adapter = create_adapter("traci", normalizer)
        # The adapter object must be created; it raises RuntimeError only when start() is called
        assert adapter is not None

    def test_libsumo_backend_returns_adapter_without_sumo(self):
        """create_adapter('libsumo') must succeed without SUMO installed."""
        normalizer = SumoNormalizer()
        adapter = create_adapter("libsumo", normalizer)
        assert adapter is not None
