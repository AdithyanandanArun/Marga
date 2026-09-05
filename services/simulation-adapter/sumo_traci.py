"""
SumoTraciAdapter: SimulationAdapter implementation using the traci library.

traci communicates with SUMO over a TCP socket (separate process).
It is the reference implementation for testing with a live SUMO instance.

Import of traci is deferred to runtime so this module can be imported
without SUMO being installed — import errors are raised as RuntimeError
only when the adapter is actually started.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Iterable, Optional, Union

from .base import AdapterConfig
from .normalizer import SumoNormalizer
from .schemas import (
    DynamicActorObservation,
    InfrastructureState,
    PedestrianState,
    RoadState,
    VehicleState,
)

if TYPE_CHECKING:
    pass  # avoid circular imports

log = logging.getLogger(__name__)

_TRACI_NOT_INSTALLED_MSG = (
    "traci is not installed. "
    "Install it with: pip install 'marga-simulation-adapter[traci]'"
)


def _require_traci():
    """Import traci and raise a clear error if it is not available."""
    try:
        import traci  # type: ignore[import]

        return traci
    except ImportError as exc:
        raise RuntimeError(_TRACI_NOT_INSTALLED_MSG) from exc


class SumoTraciAdapter:
    """
    SimulationAdapter backed by traci (SUMO's TCP-based Python API).

    Switching to libsumo must not change the canonical output — use
    SumoLibsumoAdapter for the libsumo backend; both produce identical
    canonical types via SumoNormalizer.
    """

    # Source tag embedded in every canonical state object
    SOURCE: str = "sumo_traci"

    def __init__(self, normalizer: SumoNormalizer) -> None:
        self._normalizer = normalizer
        self._config: Optional[AdapterConfig] = None
        self._scenario_run_id: str = ""
        self._running: bool = False
        self._connection = None  # traci connection handle
        self._current_time: float = 0.0

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self, config: dict) -> None:
        """
        Parse config, build the SUMO command and connect via traci.

        Raises RuntimeError if traci is not installed.
        """
        traci = _require_traci()

        self._config = AdapterConfig(**config)
        cfg = self._config

        cmd = ["sumo", "-n", cfg.sumo_net_file, "-r", cfg.sumo_route_file]

        if cfg.sumo_config_file:
            cmd += ["-c", cfg.sumo_config_file]

        cmd += [
            "--step-length", str(cfg.step_length_s),
            "--seed", str(cfg.seed),
            "--no-step-log", "true",
            "--no-warnings", "true",
        ]

        if cfg.gui:
            cmd[0] = "sumo-gui"

        log.info("Starting SUMO via traci: %s", " ".join(cmd))
        traci.start(cmd, port=cfg.port)
        self._connection = traci

        # Initialise coordinate conversion from the net boundary
        try:
            boundary = traci.simulation.getNetBoundary()
            # boundary = ((x_min, y_min), (x_max, y_max))
            # The net offset is not directly available via traci; we use
            # the origin lat/lon from the config if provided.
            self._normalizer.set_net_offset(
                net_offset_x=0.0,
                net_offset_y=0.0,
                origin_lat=cfg.sumo_origin_lat,
                origin_lon=cfg.sumo_origin_lon,
            )
            log.debug("Net boundary: %s", boundary)
        except Exception as exc:
            log.warning("Could not read net boundary: %s", exc)

        self._running = True
        self._current_time = 0.0
        log.info("SUMO simulation started (scenario_run_id=%s)", self._scenario_run_id)

    def stop(self) -> None:
        """Close the traci connection and mark the adapter as stopped."""
        if self._running and self._connection is not None:
            try:
                self._connection.close()
                log.info("SUMO simulation stopped.")
            except Exception as exc:
                log.warning("Error while closing traci connection: %s", exc)
            finally:
                self._running = False
                self._connection = None

    # ------------------------------------------------------------------
    # Simulation step
    # ------------------------------------------------------------------

    def step(self, dt: float) -> None:
        """Advance simulation by one step (dt is advisory; SUMO uses step-length from config)."""
        traci = _require_traci()
        traci.simulationStep()
        self._current_time += dt

    # ------------------------------------------------------------------
    # Actor enumeration
    # ------------------------------------------------------------------

    def list_actors(
        self,
    ) -> Iterable[Union[VehicleState, PedestrianState, DynamicActorObservation]]:
        """Yield canonical state for every vehicle and pedestrian in the sim."""
        traci = _require_traci()
        now = datetime.now(timezone.utc)

        # Vehicles
        for vid in traci.vehicle.getIDList():
            try:
                x, y = traci.vehicle.getPosition(vid)
                raw = {
                    "x": x,
                    "y": y,
                    "speed": traci.vehicle.getSpeed(vid),
                    "angle": traci.vehicle.getAngle(vid),
                    "type_id": traci.vehicle.getTypeID(vid),
                    "acceleration": traci.vehicle.getAcceleration(vid),
                    "edge_id": traci.vehicle.getRoadID(vid),
                    "lane_id": traci.vehicle.getLaneID(vid),
                }
                yield self._normalizer.normalize_vehicle_state(
                    vehicle_id=vid,
                    raw=raw,
                    timestamp=now,
                    scenario_run_id=self._scenario_run_id,
                    source=self.SOURCE,
                )
            except Exception as exc:
                log.warning("Failed to read state for vehicle %s: %s", vid, exc)

        # Pedestrians / persons
        for pid in traci.person.getIDList():
            try:
                x, y = traci.person.getPosition(pid)
                raw = {
                    "x": x,
                    "y": y,
                    "speed": traci.person.getSpeed(pid),
                    "angle": traci.person.getAngle(pid),
                    "edge_id": traci.person.getRoadID(pid),
                }
                yield self._normalizer.normalize_pedestrian_state(
                    ped_id=pid,
                    raw=raw,
                    timestamp=now,
                    scenario_run_id=self._scenario_run_id,
                    source=self.SOURCE,
                )
            except Exception as exc:
                log.warning("Failed to read state for pedestrian %s: %s", pid, exc)

    # ------------------------------------------------------------------
    # Signal states
    # ------------------------------------------------------------------

    def get_signal_states(self) -> list[InfrastructureState]:
        """Return canonical InfrastructureState for every traffic light."""
        traci = _require_traci()
        now = datetime.now(timezone.utc)
        states: list[InfrastructureState] = []

        for tls_id in traci.trafficlight.getIDList():
            try:
                phase_string = traci.trafficlight.getRedYellowGreenState(tls_id)
                next_switch = traci.trafficlight.getNextSwitch(tls_id)
                phase_remaining_s = max(0.0, next_switch - traci.simulation.getTime())

                # Get position from controlled junction(s)
                controlled = traci.trafficlight.getControlledJunctions(tls_id)
                if controlled:
                    jx, jy = traci.junction.getPosition(controlled[0])
                    lat, lon = self._normalizer.sumo_to_wgs84(jx, jy)
                else:
                    lat, lon = 0.0, 0.0

                raw = {
                    "phase_string": phase_string,
                    "phase_remaining_s": phase_remaining_s,
                    "operational": True,
                }
                states.append(
                    self._normalizer.normalize_signal_state(
                        tls_id=tls_id,
                        raw=raw,
                        position=(lat, lon),
                        timestamp=now,
                        scenario_run_id=self._scenario_run_id,
                        source=self.SOURCE,
                    )
                )
            except Exception as exc:
                log.warning("Failed to read signal state for %s: %s", tls_id, exc)

        return states

    # ------------------------------------------------------------------
    # Road states
    # ------------------------------------------------------------------

    def get_road_states(self) -> list[RoadState]:
        """Return canonical RoadState for every edge in the network."""
        traci = _require_traci()
        now = datetime.now(timezone.utc)
        states: list[RoadState] = []

        for edge_id in traci.edge.getIDList():
            try:
                lane_count = traci.edge.getLaneNumber(edge_id)
                speed_limit = traci.edge.getAllowedSpeed(edge_id)
                raw = {
                    "lane_count": lane_count,
                    "lanes_available": lane_count,
                    "speed_limit_mps": speed_limit,
                    "road_condition": "clear",
                }
                states.append(
                    self._normalizer.normalize_road_state(
                        edge_id=edge_id,
                        raw=raw,
                        timestamp=now,
                        scenario_run_id=self._scenario_run_id,
                        source=self.SOURCE,
                    )
                )
            except Exception as exc:
                log.warning("Failed to read road state for edge %s: %s", edge_id, exc)

        return states

    # ------------------------------------------------------------------
    # Command injection
    # ------------------------------------------------------------------

    def apply_vehicle_command(self, command: dict) -> None:
        """
        Apply a vehicle control command.

        Supported actions: "setSpeed", "setRoute", "remove".
        """
        traci = _require_traci()
        vehicle_id: str = command["vehicle_id"]
        action: str = command["action"]
        value = command.get("value")

        if action == "setSpeed":
            traci.vehicle.setSpeed(vehicle_id, float(value))
        elif action == "setRoute":
            traci.vehicle.setRoute(vehicle_id, list(value))
        elif action == "remove":
            traci.vehicle.remove(vehicle_id)
        else:
            log.warning("Unknown vehicle action: %s", action)

    def apply_signal_command(self, command: dict) -> None:
        """
        Override a traffic signal phase.

        Supported keys: signal_id, phase (phase string), duration_s.
        """
        traci = _require_traci()
        signal_id: str = command["signal_id"]

        if "phase" in command:
            phase_str: str = command["phase"]
            duration_s: float = float(command.get("duration_s", 30.0))
            traci.trafficlight.setRedYellowGreenState(signal_id, phase_str)
            traci.trafficlight.setPhaseDuration(signal_id, duration_s)
        elif "phase_index" in command:
            traci.trafficlight.setPhase(signal_id, int(command["phase_index"]))
        else:
            log.warning("apply_signal_command: no phase or phase_index provided")

    def apply_road_event(self, event: dict) -> None:
        """
        Apply a road event (closure, narrowing, speed limit change).

        Supported event_types: "close", "narrow", "speed_limit".
        """
        traci = _require_traci()
        edge_id: str = event["edge_id"]
        event_type: str = event["event_type"]
        value = event.get("value")

        if event_type == "close":
            # Disallow all vehicle classes → effectively closes the edge
            for lane_idx in range(traci.edge.getLaneNumber(edge_id)):
                lane_id = f"{edge_id}_{lane_idx}"
                traci.lane.setAllowed(lane_id, [])
        elif event_type == "narrow":
            # Close the rightmost lane(s) based on value (number to close)
            n_close = int(value) if value is not None else 1
            lane_count = traci.edge.getLaneNumber(edge_id)
            for lane_idx in range(min(n_close, lane_count)):
                lane_id = f"{edge_id}_{lane_idx}"
                traci.lane.setAllowed(lane_id, [])
        elif event_type == "speed_limit":
            traci.edge.setMaxSpeed(edge_id, float(value))
        else:
            log.warning("Unknown road event_type: %s", event_type)

    # ------------------------------------------------------------------
    # Reset
    # ------------------------------------------------------------------

    def reset(self, scenario_run_id: str) -> None:
        """Stop the current simulation and prepare for a new run."""
        if self._running:
            self.stop()
        self._scenario_run_id = scenario_run_id
        self._current_time = 0.0
        log.info("Adapter reset for scenario_run_id=%s", scenario_run_id)

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def current_time(self) -> float:
        return self._current_time

    @property
    def scenario_run_id(self) -> str:
        return self._scenario_run_id
