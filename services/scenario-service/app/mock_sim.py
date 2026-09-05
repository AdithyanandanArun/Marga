"""
Deterministic Bangalore junction mock simulation for the Marga scenario service.

Models the Shivajinagar Junction resilience scenario:
  - 25 lane-constrained actors on the junction approach roads
  - 7-phase event sequence (normal → conflict → GPS/internet degradation → resolution)
  - Publishes canonical VehicleState, ConnectivityEvent, PositionQualityEvent to gateway
  - Fixed seed guarantees identical event sequences across runs

Junction center: 12.9822°N, 77.5935°E  (Shivajinagar, Bangalore)
"""

from __future__ import annotations

import asyncio
import logging
import math
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .schemas import ScenarioDefinition

logger = logging.getLogger("marga.scenario.mock_sim")

# ---------------------------------------------------------------------------
# Junction geometry — Shivajinagar Junction, Bangalore
# ---------------------------------------------------------------------------

_JCT_LAT = 12.9822
_JCT_LON = 77.5935

# 1° lat  ≈ 111 320 m;  1° lon at this latitude ≈ 108 506 m
_M_PER_DEG_LAT = 111_320.0
_M_PER_DEG_LON = 111_320.0 * math.cos(math.radians(_JCT_LAT))


def _m_to_dlat(m: float) -> float:
    return m / _M_PER_DEG_LAT


def _m_to_dlon(m: float) -> float:
    return m / _M_PER_DEG_LON


def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6_371_000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return 2 * r * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dlon = math.radians(lon2 - lon1)
    x = math.sin(dlon) * math.cos(phi2)
    y = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(dlon)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


# Route waypoints: approach → junction center → exit
# Each point is (lat, lon).
# Actors start at waypoint[0] and move toward waypoint[-1].
_ROUTES: dict[str, list[tuple[float, float]]] = {
    # West approach → east exit  (ego_auto lane: offset +1 lane north)
    "west_east": [
        (_JCT_LAT + _m_to_dlat(2), _JCT_LON - _m_to_dlon(200)),
        (_JCT_LAT + _m_to_dlat(2), _JCT_LON - _m_to_dlon(80)),
        (_JCT_LAT, _JCT_LON),
        (_JCT_LAT - _m_to_dlat(2), _JCT_LON + _m_to_dlon(80)),
        (_JCT_LAT - _m_to_dlat(2), _JCT_LON + _m_to_dlon(200)),
    ],
    # South approach → north exit  (conflict_bus lane: offset -1 lane west)
    "south_north": [
        (_JCT_LAT - _m_to_dlat(200), _JCT_LON - _m_to_dlon(2)),
        (_JCT_LAT - _m_to_dlat(80), _JCT_LON - _m_to_dlon(2)),
        (_JCT_LAT, _JCT_LON),
        (_JCT_LAT + _m_to_dlat(80), _JCT_LON + _m_to_dlon(2)),
        (_JCT_LAT + _m_to_dlat(200), _JCT_LON + _m_to_dlon(2)),
    ],
    # North approach → south exit  (bg_car_1 lane: offset +1 lane east)
    "north_south": [
        (_JCT_LAT + _m_to_dlat(220), _JCT_LON + _m_to_dlon(2)),
        (_JCT_LAT + _m_to_dlat(90), _JCT_LON + _m_to_dlon(2)),
        (_JCT_LAT, _JCT_LON),
        (_JCT_LAT - _m_to_dlat(90), _JCT_LON - _m_to_dlon(2)),
        (_JCT_LAT - _m_to_dlat(220), _JCT_LON - _m_to_dlon(2)),
    ],
    # East approach → west exit  (bg_car_2 lane: offset -1 lane south)
    "east_west": [
        (_JCT_LAT - _m_to_dlat(2), _JCT_LON + _m_to_dlon(210)),
        (_JCT_LAT - _m_to_dlat(2), _JCT_LON + _m_to_dlon(85)),
        (_JCT_LAT, _JCT_LON),
        (_JCT_LAT + _m_to_dlat(2), _JCT_LON - _m_to_dlon(85)),
        (_JCT_LAT + _m_to_dlat(2), _JCT_LON - _m_to_dlon(210)),
    ],
    # West approach → east exit, motorcycle (overtaking lane, offset further north)
    "west_east_moto": [
        (_JCT_LAT + _m_to_dlat(4), _JCT_LON - _m_to_dlon(180)),
        (_JCT_LAT + _m_to_dlat(4), _JCT_LON - _m_to_dlon(70)),
        (_JCT_LAT, _JCT_LON),
        (_JCT_LAT - _m_to_dlat(4), _JCT_LON + _m_to_dlon(70)),
        (_JCT_LAT - _m_to_dlat(4), _JCT_LON + _m_to_dlon(180)),
    ],
}

_CANONICAL_ACTOR_TYPE = {
    "auto_rickshaw": "AUTO",
    "bus": "BUS",
    "car": "CAR",
    "motorcycle": "BIKE",
}

_SPEED_CAP: dict[str, float] = {
    "auto_rickshaw": 8.3,
    "bus": 11.1,
    "car": 13.9,
    "motorcycle": 15.3,
}


# ---------------------------------------------------------------------------
# Waypoint-following actor
# ---------------------------------------------------------------------------


@dataclass
class _Actor:
    actor_id: str
    actor_type: str
    waypoints: list[tuple[float, float]]
    speed_mps: float
    route_id: str = ""
    initial_progress_m: float = 0.0
    target_speed: float = field(init=False)
    wp_idx: int = field(default=0, init=False)
    lat: float = field(init=False)
    lon: float = field(init=False)
    heading_deg: float = field(init=False)
    uncertainty_m: float = field(default=4.0, init=False)
    road_segment_id: str = field(default="", init=False)

    def __post_init__(self) -> None:
        self.target_speed = self.speed_mps
        self.lat, self.lon = self.waypoints[0]
        if len(self.waypoints) > 1:
            self.heading_deg = _bearing(self.lat, self.lon, *self.waypoints[1])
        else:
            self.heading_deg = 0.0
        self.road_segment_id = f"jct_{self.route_id or self.actor_type}"
        if self.initial_progress_m > 0:
            self._seek(self.initial_progress_m)

    def _seek(self, distance_m: float) -> None:
        """Place a background vehicle at a deterministic point on its lane."""
        while self.wp_idx < len(self.waypoints) - 1:
            next_lat, next_lon = self.waypoints[self.wp_idx + 1]
            segment = _haversine(self.lat, self.lon, next_lat, next_lon)
            if distance_m <= segment:
                ratio = distance_m / segment if segment else 0.0
                self.lat += (next_lat - self.lat) * ratio
                self.lon += (next_lon - self.lon) * ratio
                self.heading_deg = _bearing(self.lat, self.lon, next_lat, next_lon)
                return
            distance_m -= segment
            self.wp_idx += 1
            self.lat, self.lon = next_lat, next_lon
        self.wp_idx = 0
        self.lat, self.lon = self.waypoints[0]

    def set_target_speed(self, speed_mps: float) -> None:
        self.target_speed = max(0.0, speed_mps)

    def set_uncertainty(self, uncertainty_m: float) -> None:
        self.uncertainty_m = uncertainty_m

    def step(self, dt: float) -> None:
        # Smooth acceleration toward target (±0.5 m/s² per step)
        delta = self.target_speed - self.speed_mps
        step = min(abs(delta), 0.5 * dt) * (1.0 if delta >= 0 else -1.0)
        self.speed_mps = max(0.0, self.speed_mps + step)

        if self.speed_mps < 0.01:
            return

        # Advance toward the next waypoint
        if self.wp_idx >= len(self.waypoints) - 1:
            # A deterministic loop keeps the junction populated for the full
            # demo instead of leaving an empty intersection after one pass.
            self.wp_idx = 0
            self.lat, self.lon = self.waypoints[0]

        next_lat, next_lon = self.waypoints[self.wp_idx + 1]
        dist_remaining = _haversine(self.lat, self.lon, next_lat, next_lon)

        if dist_remaining < 2.0:
            self.wp_idx += 1
            self.lat, self.lon = next_lat, next_lon
            if self.wp_idx < len(self.waypoints) - 1:
                self.heading_deg = _bearing(self.lat, self.lon, *self.waypoints[self.wp_idx + 1])
            return

        self.heading_deg = _bearing(self.lat, self.lon, next_lat, next_lon)
        travel_m = self.speed_mps * dt
        bearing_rad = math.radians(self.heading_deg)
        self.lat += (travel_m * math.cos(bearing_rad)) / _M_PER_DEG_LAT
        self.lon += (travel_m * math.sin(bearing_rad)) / _M_PER_DEG_LON

    def to_vehicle_state(self) -> dict[str, Any]:
        return {
            "actor_id": self.actor_id,
            "actor_type": _CANONICAL_ACTOR_TYPE.get(self.actor_type, "OTHER"),
            "ts": datetime.now(UTC).isoformat(),
            "position": {"lat": round(self.lat, 7), "lon": round(self.lon, 7)},
            "position_uncertainty_m": self.uncertainty_m,
            "speed_mps": round(self.speed_mps, 3),
            "heading_deg": round(self.heading_deg % 360, 2),
            "road_segment_id": self.road_segment_id,
            "source": "SIMULATION",
        }


# ---------------------------------------------------------------------------
# Seven-phase resilience event schedule
# ---------------------------------------------------------------------------

# (sim_time_s, action_fn) — applied once when sim_time crosses the threshold.
# action_fn receives (actors_by_id, client, gateway_url, run_id).

_PHASE_TIMES = [20.0, 25.0, 28.0, 32.0, 40.0, 45.0, 52.0]

_GPS_DEGRADED_M = 25.0
_GPS_NORMAL_M = 4.0


async def _post_connectivity(
    client: Any,
    gateway_url: str,
    mode: str,
    affected: list[str],
) -> None:
    payload = {
        "ts": datetime.now(UTC).isoformat(),
        "mode": mode,
        "affected_actor_ids": affected,
        "v2x_range_m": 300.0 if mode == "DIRECT_ONLY" else None,
    }
    try:
        await client.post(f"{gateway_url}/v1/ingest/connectivity", json=payload)
    except Exception as exc:
        logger.debug("connectivity post failed: %s", exc)


async def _post_position_quality(
    client: Any,
    gateway_url: str,
    actor_id: str,
    uncertainty_m: float,
) -> None:
    payload = {
        "ts": datetime.now(UTC).isoformat(),
        "actor_id": actor_id,
        "uncertainty_m": uncertainty_m,
    }
    try:
        await client.post(f"{gateway_url}/v1/ingest/position-quality", json=payload)
    except Exception as exc:
        logger.debug("position-quality post failed: %s", exc)


# ---------------------------------------------------------------------------
# Main simulation entry point
# ---------------------------------------------------------------------------


def _build_actors() -> dict[str, _Actor]:
    actors = {
        "ego_auto": _Actor(
            actor_id="ego_auto",
            actor_type="auto_rickshaw",
            waypoints=_ROUTES["west_east"],
            speed_mps=5.5,
            route_id="west_east",
        ),
        "conflict_bus": _Actor(
            actor_id="conflict_bus",
            actor_type="bus",
            waypoints=_ROUTES["south_north"],
            speed_mps=6.5,
            route_id="south_north",
        ),
        "bg_car_1": _Actor(
            actor_id="bg_car_1",
            actor_type="car",
            waypoints=_ROUTES["north_south"],
            speed_mps=8.0,
            route_id="north_south",
        ),
        "bg_car_2": _Actor(
            actor_id="bg_car_2",
            actor_type="car",
            waypoints=_ROUTES["east_west"],
            speed_mps=7.5,
            route_id="east_west",
        ),
        "bg_moto": _Actor(
            actor_id="bg_moto",
            actor_type="motorcycle",
            waypoints=_ROUTES["west_east_moto"],
            speed_mps=9.5,
            route_id="west_east_moto",
        ),
    }
    # Four evenly spaced vehicles per lane form a believable mixed-traffic
    # scene without turning the map into an unreadable 200-actor cloud. Their
    # equal per-lane speeds preserve safe headway; all use the same waypoint
    # and kinematic model as the foreground actors.
    lane_order = ["west_east", "south_north", "north_south", "east_west", "west_east_moto"]
    vehicle_types = ["car", "motorcycle", "auto_rickshaw", "car"]
    for lane_index, route_id in enumerate(lane_order):
        for slot, actor_type in enumerate(vehicle_types):
            actors[f"traffic_{route_id}_{slot + 1}"] = _Actor(
                actor_id=f"traffic_{route_id}_{slot + 1}",
                actor_type=actor_type,
                waypoints=_ROUTES[route_id],
                speed_mps=4.2 + lane_index * 0.25,
                route_id=route_id,
                initial_progress_m=42.0 + slot * 68.0 + lane_index * 4.0,
            )
    return actors


async def run_mock_simulation(
    run_id: str,
    scenario: "ScenarioDefinition",
    gateway_url: str,
    event_sink: "asyncio.Queue[list[dict[str, Any]]] | None" = None,
    tick_hz: float = 10.0,
) -> None:
    """
    Drive the Bangalore junction resilience scenario.

    Publishes canonical VehicleState events every tick plus ConnectivityEvent and
    PositionQualityEvent at each of the seven resilience phase boundaries.

    Runs until scenario.duration_s is reached or the task is cancelled.
    """
    try:
        import httpx
    except ImportError:
        logger.warning("httpx not installed — events will not reach gateway")
        httpx = None  # type: ignore[assignment]

    actors = _build_actors()
    all_ids = list(actors.keys())
    dt = 1.0 / tick_hz
    sim_time = 0.0
    phases_fired: set[int] = set()

    logger.info(
        "mock_sim started: run=%s scenario=%r actors=%d phases=7",
        run_id, scenario.name, len(actors),
    )

    client = httpx.AsyncClient(timeout=2.0) if httpx else None
    try:
        while sim_time < scenario.duration_s:
            # --- Phase events (fired exactly once per boundary crossing) ---
            for i, t in enumerate(_PHASE_TIMES):
                if sim_time >= t and i not in phases_fired:
                    phases_fired.add(i)
                    await _apply_phase(i, actors, all_ids, client, gateway_url)

            # --- Tick all actors ---
            for actor in actors.values():
                actor.step(dt)

            # --- Publish vehicle states ---
            events: list[dict[str, Any]] = []
            for actor in actors.values():
                vs = actor.to_vehicle_state()
                events.append({
                    "event_type": "actor.state.updated",
                    "timestamp_utc": vs["ts"],
                    "source": "mock_sim",
                    "sim_time_s": round(sim_time, 3),
                    "payload": {
                        "vehicle_id": actor.actor_id,
                        **vs,
                    },
                })
                if client is not None:
                    try:
                        await client.post(f"{gateway_url}/v1/ingest/vehicle-state", json=vs)
                    except Exception as exc:
                        logger.debug("vehicle-state post failed: %s", exc)

            if event_sink is not None:
                try:
                    event_sink.put_nowait(events)
                except asyncio.QueueFull:
                    pass

            sim_time += dt
            await asyncio.sleep(dt)

    except asyncio.CancelledError:
        logger.info("mock_sim cancelled: run=%s sim_time=%.1fs", run_id, sim_time)
        raise
    finally:
        if client is not None:
            await client.aclose()
        logger.info("mock_sim stopped: run=%s", run_id)


async def _apply_phase(
    phase_idx: int,
    actors: dict[str, _Actor],
    all_ids: list[str],
    client: Any,
    gateway_url: str,
) -> None:
    """Apply the resilience phase transition at the given index."""
    if phase_idx == 0:
        # t=20s: GPS starts degrading — all actors
        logger.info("Phase 1: GPS degrading to %.0fm", _GPS_DEGRADED_M)
        for actor in actors.values():
            actor.set_uncertainty(_GPS_DEGRADED_M)
            if client:
                await _post_position_quality(client, gateway_url, actor.actor_id, _GPS_DEGRADED_M)

    elif phase_idx == 1:
        # t=25s: Internet loss → DIRECT_ONLY
        logger.info("Phase 2: connectivity → DIRECT_ONLY")
        if client:
            await _post_connectivity(client, gateway_url, "DIRECT_ONLY", all_ids)

    elif phase_idx == 2:
        # t=28s: Conflict zone — ego_auto and conflict_bus brake for junction
        logger.info("Phase 3: conflict approach — ego braking, bus slowing")
        actors["ego_auto"].set_target_speed(1.0)
        actors["conflict_bus"].set_target_speed(2.0)

    elif phase_idx == 3:
        # t=32s: Full stop — risk peak; backend risk engine detects TTC≈0
        logger.info("Phase 4: conflict peak — ego stopped, bus stopped")
        actors["ego_auto"].set_target_speed(0.0)
        actors["conflict_bus"].set_target_speed(0.0)

    elif phase_idx == 4:
        # t=40s: Connectivity restoring → FULL
        logger.info("Phase 5: connectivity → FULL (restoring)")
        if client:
            await _post_connectivity(client, gateway_url, "FULL", all_ids)

    elif phase_idx == 5:
        # t=45s: GPS quality restoring
        logger.info("Phase 6: GPS quality restoring to %.0fm", _GPS_NORMAL_M)
        for actor in actors.values():
            actor.set_uncertainty(_GPS_NORMAL_M)
            if client:
                await _post_position_quality(client, gateway_url, actor.actor_id, _GPS_NORMAL_M)

    elif phase_idx == 6:
        # t=52s: Resolution — actors resume normal speed
        logger.info("Phase 7: resolution — actors resume")
        actors["ego_auto"].set_target_speed(5.5)
        actors["conflict_bus"].set_target_speed(6.0)
