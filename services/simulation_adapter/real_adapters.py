"""
Real-world adapter stubs for the Marga simulation adapter.

Each class satisfies the SimulationAdapter Protocol but connects to actual
hardware/radio interfaces instead of SUMO. These are thin stubs: they log
what they *would* do and return empty state until wired to real devices.

Switching from mock/SUMO to a real adapter requires only a factory change —
canonical output schema is unchanged.

Available backends:
  gnss   — NMEA/u-blox serial receiver
  obu    — On-Board Unit (DSRC/C-V2X radio frame over UDP)
  rsu    — Roadside Unit observation feed (UDP multicast or TCP)
  phone  — iOS/Android background location webhook
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Iterable, Union

from .schemas import (
    DynamicActorObservation,
    InfrastructureState,
    PedestrianState,
    RoadState,
    VehicleState,
)

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# GNSS receiver (NMEA/u-blox serial)
# ---------------------------------------------------------------------------


class GNSSAdapter:
    """
    Reads position fixes from a GNSS receiver over a serial port (NMEA 0183).

    Real implementation:
        pip install pyserial
        port = serial.Serial("/dev/ttyUSB0", 9600, timeout=1)
        sentence = port.readline()  # e.g. $GPGGA,...
        lat, lon = _parse_gga(sentence)

    Each fix is emitted as a single-actor VehicleState representing the host
    vehicle (this adapter is intended for the host OBU/RSU, not other actors).
    """

    SOURCE = "gnss_serial"

    def __init__(self, port: str = "/dev/ttyUSB0", baud: int = 9600) -> None:
        self._port = port
        self._baud = baud
        self._scenario_run_id = ""
        self._current_fix: dict | None = None
        log.info("GNSSAdapter initialised: port=%s baud=%d", port, baud)

    def start(self, config: dict) -> None:
        log.warning(
            "GNSSAdapter.start() — stub only. To enable, open serial port %s at %d baud "
            "and parse NMEA $GPGGA/$GPRMC sentences.",
            self._port, self._baud,
        )

    def stop(self) -> None:
        log.info("GNSSAdapter.stop()")

    def step(self, dt: float) -> None:
        # Real: read one or more NMEA sentences from self._serial_port
        pass

    def list_actors(self) -> Iterable[Union[VehicleState, PedestrianState, DynamicActorObservation]]:
        return []

    def get_signal_states(self) -> list[InfrastructureState]:
        return []

    def get_road_states(self) -> list[RoadState]:
        return []

    def apply_vehicle_command(self, command: dict) -> None:
        log.debug("GNSSAdapter: ignoring vehicle command (read-only adapter)")

    def apply_signal_command(self, command: dict) -> None:
        log.debug("GNSSAdapter: ignoring signal command (read-only adapter)")

    def apply_road_event(self, event: dict) -> None:
        log.debug("GNSSAdapter: ignoring road event (read-only adapter)")

    def reset(self, scenario_run_id: str) -> None:
        self._scenario_run_id = scenario_run_id
        self._current_fix = None

    @property
    def current_time(self) -> float:
        return datetime.now(timezone.utc).timestamp()

    @property
    def scenario_run_id(self) -> str:
        return self._scenario_run_id


# ---------------------------------------------------------------------------
# OBU — On-Board Unit (DSRC / C-V2X radio frame receiver)
# ---------------------------------------------------------------------------


class OBUAdapter:
    """
    Receives V2X BSM (Basic Safety Messages) from nearby OBUs over DSRC/C-V2X.

    Real implementation:
        Listen on UDP port 5900 for WSMP or ETSI ITS-G5 frames.
        Decode ASN.1 J2735 BasicSafetyMessage to extract lat/lon/speed/heading.
        Each decoded BSM becomes one VehicleState in list_actors().

    Range: up to 300 m in urban India conditions (obstacle attenuation).
    """

    SOURCE = "obu_dsrc"

    def __init__(self, listen_host: str = "0.0.0.0", listen_port: int = 5900) -> None:
        self._host = listen_host
        self._port = listen_port
        self._scenario_run_id = ""
        self._actors: dict[str, VehicleState] = {}
        log.info("OBUAdapter initialised: %s:%d", listen_host, listen_port)

    def start(self, config: dict) -> None:
        log.warning(
            "OBUAdapter.start() — stub only. To enable, bind UDP socket on %s:%d "
            "and decode J2735 BasicSafetyMessage ASN.1 frames.",
            self._host, self._port,
        )

    def stop(self) -> None:
        self._actors.clear()

    def step(self, dt: float) -> None:
        # Real: drain UDP recv buffer, decode BSMs, update self._actors
        pass

    def list_actors(self) -> Iterable[Union[VehicleState, PedestrianState, DynamicActorObservation]]:
        return list(self._actors.values())

    def get_signal_states(self) -> list[InfrastructureState]:
        return []

    def get_road_states(self) -> list[RoadState]:
        return []

    def apply_vehicle_command(self, command: dict) -> None:
        # Real: encode command as a J2735 frame and broadcast on DSRC
        log.debug("OBUAdapter: would broadcast vehicle command over DSRC: %s", command)

    def apply_signal_command(self, command: dict) -> None:
        log.debug("OBUAdapter: ignoring signal command (signal control is RSU-side)")

    def apply_road_event(self, event: dict) -> None:
        log.debug("OBUAdapter: ignoring road event")

    def reset(self, scenario_run_id: str) -> None:
        self._scenario_run_id = scenario_run_id
        self._actors.clear()

    @property
    def current_time(self) -> float:
        return datetime.now(timezone.utc).timestamp()

    @property
    def scenario_run_id(self) -> str:
        return self._scenario_run_id


# ---------------------------------------------------------------------------
# RSU — Roadside Unit observation feed
# ---------------------------------------------------------------------------


class RSUAdapter:
    """
    Receives actor observations from RSU sensors over UDP multicast or TCP.

    Real implementation:
        RSUs broadcast detected actor positions from radar/lidar/camera fusion.
        Each observation is an RSU-specific binary or JSON frame.
        Decode frame → DynamicActorObservation (confidence < 1.0 unless confirmed).

    Source tag: "rsu_<rsu_id>"
    """

    SOURCE = "rsu"

    def __init__(
        self,
        rsu_id: str = "rsu_001",
        feed_host: str = "239.0.0.1",
        feed_port: int = 5901,
        coverage_m: float = 300.0,
    ) -> None:
        self._rsu_id = rsu_id
        self._host = feed_host
        self._port = feed_port
        self._coverage_m = coverage_m
        self._scenario_run_id = ""
        self._observations: list[DynamicActorObservation] = []
        log.info(
            "RSUAdapter initialised: rsu=%s feed=%s:%d coverage=%.0fm",
            rsu_id, feed_host, feed_port, coverage_m,
        )

    def start(self, config: dict) -> None:
        log.warning(
            "RSUAdapter.start() — stub only. To enable, join UDP multicast %s:%d "
            "and decode RSU observation frames for RSU %s.",
            self._host, self._port, self._rsu_id,
        )

    def stop(self) -> None:
        self._observations.clear()

    def step(self, dt: float) -> None:
        # Real: read latest observations from the RSU multicast feed
        pass

    def list_actors(self) -> Iterable[Union[VehicleState, PedestrianState, DynamicActorObservation]]:
        return list(self._observations)

    def get_signal_states(self) -> list[InfrastructureState]:
        # RSUs may report nearby signal phases if equipped with SPaT receiver
        return []

    def get_road_states(self) -> list[RoadState]:
        return []

    def apply_vehicle_command(self, command: dict) -> None:
        log.debug("RSUAdapter: ignoring vehicle command (read-only sensor)")

    def apply_signal_command(self, command: dict) -> None:
        # Real: forward phase override to the traffic controller via SNMP/ITS-G5
        log.debug("RSUAdapter: would forward signal command to TLC: %s", command)

    def apply_road_event(self, event: dict) -> None:
        log.debug("RSUAdapter: ignoring road event")

    def reset(self, scenario_run_id: str) -> None:
        self._scenario_run_id = scenario_run_id
        self._observations.clear()

    @property
    def current_time(self) -> float:
        return datetime.now(timezone.utc).timestamp()

    @property
    def scenario_run_id(self) -> str:
        return self._scenario_run_id


# ---------------------------------------------------------------------------
# PhoneGPS — iOS/Android background location webhook
# ---------------------------------------------------------------------------


class PhoneGPSAdapter:
    """
    Receives positions from a mobile app posting GPS fixes over HTTPS.

    Real implementation:
        App (iOS CoreLocation / Android FusedLocationProvider) POSTs to
        POST /v1/adapters/phone-gps/fix  with body:
          { "device_id": "...", "lat": ..., "lon": ..., "accuracy_m": ...,
            "speed_mps": ..., "heading_deg": ..., "ts": "ISO8601" }

        This adapter should be paired with a FastAPI webhook endpoint
        (not included here) that calls update_fix() on ingest.

    Update rate: ~1 Hz in foreground, ~0.1 Hz in background (iOS throttles).
    """

    SOURCE = "phone_gps"

    def __init__(self) -> None:
        self._scenario_run_id = ""
        self._fixes: dict[str, VehicleState] = {}

    def update_fix(self, device_id: str, fix: dict) -> None:
        """Called by the webhook handler when a phone posts a new GPS fix."""
        log.debug("PhoneGPSAdapter: fix from %s: lat=%.6f lon=%.6f", device_id, fix.get("lat", 0), fix.get("lon", 0))
        # Real: construct VehicleState and store in self._fixes[device_id]

    def start(self, config: dict) -> None:
        log.warning(
            "PhoneGPSAdapter.start() — stub only. Wire a POST /v1/adapters/phone-gps/fix "
            "FastAPI endpoint that calls adapter.update_fix() for each incoming GPS fix."
        )

    def stop(self) -> None:
        self._fixes.clear()

    def step(self, dt: float) -> None:
        pass  # fixes are pushed by update_fix(), not polled

    def list_actors(self) -> Iterable[Union[VehicleState, PedestrianState, DynamicActorObservation]]:
        return list(self._fixes.values())

    def get_signal_states(self) -> list[InfrastructureState]:
        return []

    def get_road_states(self) -> list[RoadState]:
        return []

    def apply_vehicle_command(self, command: dict) -> None:
        log.debug("PhoneGPSAdapter: ignoring vehicle command (phone is passive)")

    def apply_signal_command(self, command: dict) -> None:
        log.debug("PhoneGPSAdapter: ignoring signal command")

    def apply_road_event(self, event: dict) -> None:
        log.debug("PhoneGPSAdapter: ignoring road event")

    def reset(self, scenario_run_id: str) -> None:
        self._scenario_run_id = scenario_run_id
        self._fixes.clear()

    @property
    def current_time(self) -> float:
        return datetime.now(timezone.utc).timestamp()

    @property
    def scenario_run_id(self) -> str:
        return self._scenario_run_id
