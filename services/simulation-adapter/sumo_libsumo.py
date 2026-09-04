"""
SumoLibsumoAdapter: SimulationAdapter implementation using libsumo.

libsumo is a shared-library version of SUMO's Python API that runs in-process
(no TCP socket, no separate SUMO process).  It is significantly faster than
traci for large-scale simulations.

The API is intentionally compatible with traci, so this adapter mirrors
SumoTraciAdapter almost exactly — the only differences are:
  - ``import libsumo as traci`` (API-compatible, so all traci calls work)
  - No port / TCP connection needed
  - Source tag is "sumo_libsumo"
  - Uses ``libsumo.start()`` rather than ``traci.start()``

Switching between traci and libsumo must NOT change the canonical output schema.
Both adapters produce identical VehicleState / PedestrianState / etc. objects
because the normalisation is handled entirely in SumoNormalizer.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Iterable, Optional, Union

from .base import AdapterConfig
from .normalizer import SumoNormalizer
from .schemas import (
    DynamicActorObservation,
    InfrastructureState,
    PedestrianState,
    RoadState,
    VehicleState,
)

log = logging.getLogger(__name__)

_LIBSUMO_NOT_INSTALLED_MSG = (
    "libsumo is not installed. "
    "Install it with: pip install 'marga-simulation-adapter[libsumo]'"
)


def _require_libsumo():
    """Import libsumo and raise a clear error if it is not available."""
    try:
        import libsumo as traci  # type: ignore[import]

        return traci
    except ImportError as exc:
        raise RuntimeError(_LIBSUMO_NOT_INSTALLED_MSG) from exc


class SumoLibsumoAdapter:
    """
    SimulationAdapter backed by libsumo (in-process SUMO library).

    Canonical output is identical to SumoTraciAdapter — the only observable
    difference is ``source == "sumo_libsumo"`` in every state object.
    """

    SOURCE: str = "sumo_libsumo"

    def __init__(self, normalizer: SumoNormalizer) -> None:
        self._normalizer = normalizer
        self._config: Optional[AdapterConfig] = None
        self._scenario_run_id: str = ""
        self._running: bool = False
        self._current_time: float = 0.0

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self, config: dict) -> None:
        """
        Parse config and start the in-process libsumo simulation.

        Raises RuntimeError if libsumo is not installed.
        """
        libsumo = _require_libsumo()

        self._config = AdapterConfig(**config)
        cfg = self._config

        cmd = [
            "-n", cfg.sumo_net_file,
            "-r", cfg.sumo_route_file,
            "--step-length", str(cfg.step_length_s),
            "--seed", str(cfg.seed),
            "--no-step-log", "true",
            "--no-warnings", "true",
        ]

        if cfg.sumo_config_file:
            cmd = ["-c", cfg.sumo_config_file] + cmd

        log.info("Starting SUMO via libsumo: %s", " ".join(cmd))
        # libsumo uses start() with the argument list (no process, no port)
        libsumo.start(cmd)

        # Coordinate conversion initialisation
        try:
            self._normalizer.set_net_offset(
                net_offset_x=0.0,
                net_offset_y=0.0,
                origin_lat=cfg.sumo_origin_lat,
                origin_lon=cfg.sumo_origin_lon,
            )
        except Exception as exc:
            log.warning("Could not initialise net offset: %s", exc)

        self._running = True
        self._current_time = 0.0
        log.info("libsumo simulation started (scenario_run_id=%s)", self._scenario_run_id)

    def stop(self) -> None:
        """Close the libsumo simulation."""
        if self._running:
            try:
                libsumo = _require_libsumo()
                libsumo.close()
                log.info("libsumo simulation stopped.")
            except Exception as exc:
                log.warning("Error while closing libsumo: %s", exc)
            finally:
                self._running = False

    # ------------------------------------------------------------------
    # Simulation step
    # ------------------------------------------------------------------

    def step(self, dt: float) -> None:
        """Advance simulation by one step."""
        libsumo = _require_libsumo()
        libsumo.simulationStep()
        self._current_time += dt

    # ------------------------------------------------------------------
    # Actor enumeration
    # ------------------------------------------------------------------

    def list_actors(
        self,
    ) -> Iterable[Union[VehicleState, PedestrianState, DynamicActorObservation]]:
        """Yield canonical state for every vehicle and pedestrian."""
        libsumo = _require_libsumo()
        now = datetime.now(timezone.utc)

        # Vehicles
        for vid in libsumo.vehicle.getIDList():
            try:
                x, y = libsumo.vehicle.getPosition(vid)
                raw = {
                    "x": x,
                    "y": y,
                    "speed": libsumo.vehicle.getSpeed(vid),
                    "angle": libsumo.vehicle.getAngle(vid),
                    "type_id": libsumo.vehicle.getTypeID(vid),
                    "acceleration": libsumo.vehicle.getAcceleration(vid),
                }
                yield self._normalizer.normalize_vehicle_state(
                    vehicle_id=vid,
                    raw=raw,
                    timestamp=now,
                    scenario_run_id=self._scenario_run_id,
                    source=self.SOURCE,
                )
            except Exception as exc:
                log.warning("Failed to read vehicle %s: %s", vid, exc)

        # Pedestrians / persons
        for pid in libsumo.person.getIDList():
            try:
                x, y = libsumo.person.getPosition(pid)
                raw = {
                    "x": x,
                    "y": y,
                    "speed": libsumo.person.getSpeed(pid),
                    "angle": libsumo.person.getAngle(pid),
                }
                yield self._normalizer.normalize_pedestrian_state(
                    ped_id=pid,
                    raw=raw,
                    timestamp=now,
                    scenario_run_id=self._scenario_run_id,
                    source=self.SOURCE,
                )
            except Exception as exc:
                log.warning("Failed to read pedestrian %s: %s", pid, exc)

    # ------------------------------------------------------------------
    # Signal states
    # ------------------------------------------------------------------

    def get_signal_states(self) -> list[InfrastructureState]:
        """Return canonical InfrastructureState for every traffic light."""
        libsumo = _require_libsumo()
        now = datetime.now(timezone.utc)
        states: list[InfrastructureState] = []

        for tls_id in libsumo.trafficlight.getIDList():
            try:
                phase_string = libsumo.trafficlight.getRedYellowGreenState(tls_id)
                next_switch = libsumo.trafficlight.getNextSwitch(tls_id)
                phase_remaining_s = max(0.0, next_switch - libsumo.simulation.getTime())

                controlled = libsumo.trafficlight.getControlledJunctions(tls_id)
                if controlled:
                    jx, jy = libsumo.junction.getPosition(controlled[0])
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
                log.warning("Failed to read signal %s: %s", tls_id, exc)

        return states

    # ------------------------------------------------------------------
    # Road states
    # ------------------------------------------------------------------

    def get_road_states(self) -> list[RoadState]:
        """Return canonical RoadState for every edge in the network."""
        libsumo = _require_libsumo()
        now = datetime.now(timezone.utc)
        states: list[RoadState] = []

        for edge_id in libsumo.edge.getIDList():
            try:
                lane_count = libsumo.edge.getLaneNumber(edge_id)
                speed_limit = libsumo.edge.getAllowedSpeed(edge_id)
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
                log.warning("Failed to read edge %s: %s", edge_id, exc)

        return states

    # ------------------------------------------------------------------
    # Command injection
    # ------------------------------------------------------------------

    def apply_vehicle_command(self, command: dict) -> None:
        """Apply a vehicle control command (setSpeed, setRoute, remove)."""
        libsumo = _require_libsumo()
        vehicle_id: str = command["vehicle_id"]
        action: str = command["action"]
        value = command.get("value")

        if action == "setSpeed":
            libsumo.vehicle.setSpeed(vehicle_id, float(value))
        elif action == "setRoute":
            libsumo.vehicle.setRoute(vehicle_id, list(value))
        elif action == "remove":
            libsumo.vehicle.remove(vehicle_id)
        else:
            log.warning("Unknown vehicle action: %s", action)

    def apply_signal_command(self, command: dict) -> None:
        """Override a traffic signal phase."""
        libsumo = _require_libsumo()
        signal_id: str = command["signal_id"]

        if "phase" in command:
            phase_str: str = command["phase"]
            duration_s: float = float(command.get("duration_s", 30.0))
            libsumo.trafficlight.setRedYellowGreenState(signal_id, phase_str)
            libsumo.trafficlight.setPhaseDuration(signal_id, duration_s)
        elif "phase_index" in command:
            libsumo.trafficlight.setPhase(signal_id, int(command["phase_index"]))
        else:
            log.warning("apply_signal_command: no phase or phase_index provided")

    def apply_road_event(self, event: dict) -> None:
        """Apply a road event (close, narrow, speed_limit)."""
        libsumo = _require_libsumo()
        edge_id: str = event["edge_id"]
        event_type: str = event["event_type"]
        value = event.get("value")

        if event_type == "close":
            for lane_idx in range(libsumo.edge.getLaneNumber(edge_id)):
                lane_id = f"{edge_id}_{lane_idx}"
                libsumo.lane.setAllowed(lane_id, [])
        elif event_type == "narrow":
            n_close = int(value) if value is not None else 1
            lane_count = libsumo.edge.getLaneNumber(edge_id)
            for lane_idx in range(min(n_close, lane_count)):
                lane_id = f"{edge_id}_{lane_idx}"
                libsumo.lane.setAllowed(lane_id, [])
        elif event_type == "speed_limit":
            libsumo.edge.setMaxSpeed(edge_id, float(value))
        else:
            log.warning("Unknown road event_type: %s", event_type)

    # ------------------------------------------------------------------
    # Reset
    # ------------------------------------------------------------------

    def reset(self, scenario_run_id: str) -> None:
        """Stop and prepare for a new scenario run."""
        if self._running:
            self.stop()
        self._scenario_run_id = scenario_run_id
        self._current_time = 0.0
        log.info("libsumo adapter reset for scenario_run_id=%s", scenario_run_id)

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def current_time(self) -> float:
        return self._current_time

    @property
    def scenario_run_id(self) -> str:
        return self._scenario_run_id
