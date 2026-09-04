"""
Self-contained mock simulation for the Marga scenario service.

Generates synthetic actors based on scenario parameters without SUMO.
Each scenario run spawns a background asyncio task that ticks at 10 Hz and
POSTs canonical events to the gateway's /v1/world-state/ingest endpoint.
"""

from __future__ import annotations

import asyncio
import logging
import math
import random
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .schemas import ScenarioDefinition

logger = logging.getLogger("marga.scenario.mock_sim")

# Bangalore Central bounding box (lat/lon)
_LAT_MIN, _LAT_MAX = 12.9500, 13.0200
_LON_MIN, _LON_MAX = 77.5400, 77.6400

# Top speed per vehicle type in m/s (India urban)
_SPEED_CAP: dict[str, float] = {
    "car": 13.9,
    "truck": 9.7,
    "bus": 11.1,
    "motorcycle": 13.9,
    "auto_rickshaw": 8.3,
    "bicycle": 5.6,
}

# How many actors each composition fraction maps to (base pool of 40)
_BASE_POOL = 40


class _Actor:
    __slots__ = ("actor_id", "actor_type", "lat", "lon", "speed", "heading")

    def __init__(
        self,
        actor_id: str,
        actor_type: str,
        lat: float,
        lon: float,
        speed: float,
        heading: float,
    ) -> None:
        self.actor_id = actor_id
        self.actor_type = actor_type
        self.lat = lat
        self.lon = lon
        self.speed = speed
        self.heading = heading

    def step(self, dt: float, rng: random.Random) -> None:
        cap = _SPEED_CAP.get(self.actor_type, 10.0)
        self.speed = max(0.0, min(cap, self.speed + rng.uniform(-0.4, 0.4)))
        self.heading = (self.heading + rng.uniform(-4.0, 4.0)) % 360

        dist_m = self.speed * dt
        bearing = math.radians(self.heading)
        dlat = dist_m * math.cos(bearing) / 111_320.0
        dlon = dist_m * math.sin(bearing) / (111_320.0 * math.cos(math.radians(self.lat)))

        self.lat += dlat
        self.lon += dlon

        # Bounce off bounding box
        if not (_LAT_MIN <= self.lat <= _LAT_MAX):
            self.heading = (180.0 - self.heading) % 360
            self.lat = max(_LAT_MIN, min(_LAT_MAX, self.lat))
        if not (_LON_MIN <= self.lon <= _LON_MAX):
            self.heading = (360.0 - self.heading) % 360
            self.lon = max(_LON_MIN, min(_LON_MAX, self.lon))

    def to_event(self, run_id: str) -> dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat()
        return {
            "event_type": "actor.state.updated",
            "timestamp_utc": now,
            "source": "mock_sim",
            "trace_id": str(uuid.uuid4()),
            "payload": {
                "vehicle_id": self.actor_id,
                "timestamp_utc": now,
                "position": {
                    "lat": self.lat,
                    "lon": self.lon,
                    "uncertainty_m": 2.0,
                    "confidence": 0.98,
                    "source": "mock_sim",
                },
                "speed_mps": round(self.speed, 3),
                "heading_deg": round(self.heading, 2),
                "vehicle_type": self.actor_type,
                "source": "mock_sim",
                "scenario_run_id": run_id,
            },
        }


def _build_actors(scenario: "ScenarioDefinition", rng: random.Random) -> list[_Actor]:
    tc = scenario.traffic_composition
    fractions: list[tuple[str, float]] = [
        ("car", tc.car_fraction),
        ("truck", tc.truck_fraction),
        ("bus", tc.bus_fraction),
        ("motorcycle", tc.motorcycle_fraction),
        ("auto_rickshaw", tc.auto_rickshaw_fraction),
        ("bicycle", tc.bicycle_fraction),
    ]
    actors: list[_Actor] = []
    for atype, frac in fractions:
        count = max(1, round(_BASE_POOL * frac))
        cap = _SPEED_CAP.get(atype, 10.0)
        for i in range(count):
            actors.append(
                _Actor(
                    actor_id=f"{atype}-{i:04d}",
                    actor_type=atype,
                    lat=rng.uniform(_LAT_MIN, _LAT_MAX),
                    lon=rng.uniform(_LON_MIN, _LON_MAX),
                    speed=rng.uniform(0.5, cap * 0.6),
                    heading=rng.uniform(0, 360),
                )
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
    Drive a mock simulation tick loop for the given scenario run.

    Ticks at tick_hz, posting actor events to the gateway and optionally
    pushing them into event_sink for replay recording.

    Runs until cancelled (via asyncio.Task.cancel()).
    """
    try:
        import httpx
    except ImportError:
        logger.warning("httpx not installed — mock sim events will not reach gateway")
        httpx = None  # type: ignore[assignment]

    rng = random.Random(scenario.seed)
    actors = _build_actors(scenario, rng)
    dt = 1.0 / tick_hz
    sim_time = 0.0

    logger.info(
        "mock_sim started: run=%s scenario=%r actors=%d",
        run_id, scenario.name, len(actors),
    )

    client = httpx.AsyncClient(timeout=2.0) if httpx else None
    try:
        while sim_time < scenario.duration_s:
            for actor in actors:
                actor.step(dt, rng)

            events = [a.to_event(run_id) for a in actors]

            if event_sink is not None:
                try:
                    event_sink.put_nowait(events)
                except asyncio.QueueFull:
                    pass

            if client is not None:
                try:
                    await client.post(
                        f"{gateway_url}/v1/world-state/ingest",
                        json={"events": events},
                    )
                except Exception as exc:
                    logger.debug("ingest post failed: %s", exc)

            sim_time += dt
            await asyncio.sleep(dt)
    except asyncio.CancelledError:
        logger.info("mock_sim cancelled: run=%s sim_time=%.1fs", run_id, sim_time)
        raise
    finally:
        if client is not None:
            await client.aclose()
        logger.info("mock_sim stopped: run=%s", run_id)
