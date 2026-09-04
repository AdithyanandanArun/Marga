#!/usr/bin/env python3
"""
Marga load generator — stress the gateway's world-state ingest endpoint.

Generates synthetic actor events and POSTs them to the gateway at a
configurable rate. Use this to validate the ingest pipeline under load
(e.g. 200 vehicles at 10 Hz = 2000 events/sec).

Usage examples:
  # 50 vehicles at 10 Hz for 30 seconds against local gateway
  python tools/load-gen/load_gen.py --vehicles 50 --hz 10 --duration 30

  # 200 vehicles against a remote gateway, quiet output
  python tools/load-gen/load_gen.py --vehicles 200 --hz 5 --url http://192.168.1.10:8000 --quiet

  # Replay a recorded fixture file at 2x speed
  python tools/load-gen/load_gen.py --fixture services/scenario-service/fixtures/bangalore_morning_rush.json --speed 2.0

Requirements: pip install httpx  (stdlib otherwise)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import random
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

try:
    import httpx
except ImportError:
    print("ERROR: httpx not installed. Run: pip install httpx", file=sys.stderr)
    sys.exit(1)

# Bangalore Central bounding box
_LAT_MIN, _LAT_MAX = 12.9500, 13.0200
_LON_MIN, _LON_MAX = 77.5400, 77.6400

_SPEED_CAP: dict[str, float] = {
    "car": 13.9,
    "truck": 9.7,
    "bus": 11.1,
    "motorcycle": 13.9,
    "auto_rickshaw": 8.3,
    "bicycle": 5.6,
}
_VEHICLE_TYPES = list(_SPEED_CAP.keys())


class _Vehicle:
    __slots__ = ("vid", "vtype", "lat", "lon", "speed", "heading")

    def __init__(self, vid: str, vtype: str, rng: random.Random) -> None:
        self.vid = vid
        self.vtype = vtype
        cap = _SPEED_CAP[vtype]
        self.lat = rng.uniform(_LAT_MIN, _LAT_MAX)
        self.lon = rng.uniform(_LON_MIN, _LON_MAX)
        self.speed = rng.uniform(1.0, cap * 0.6)
        self.heading = rng.uniform(0, 360)

    def step(self, dt: float, rng: random.Random) -> None:
        cap = _SPEED_CAP[self.vtype]
        self.speed = max(0.0, min(cap, self.speed + rng.uniform(-0.4, 0.4)))
        self.heading = (self.heading + rng.uniform(-4.0, 4.0)) % 360

        dist_m = self.speed * dt
        bearing = math.radians(self.heading)
        self.lat += dist_m * math.cos(bearing) / 111_320.0
        self.lon += dist_m * math.sin(bearing) / (111_320.0 * math.cos(math.radians(self.lat)))

        if not (_LAT_MIN <= self.lat <= _LAT_MAX):
            self.heading = (180.0 - self.heading) % 360
            self.lat = max(_LAT_MIN, min(_LAT_MAX, self.lat))
        if not (_LON_MIN <= self.lon <= _LON_MAX):
            self.heading = (360.0 - self.heading) % 360
            self.lon = max(_LON_MIN, min(_LON_MAX, self.lon))

    def to_event(self) -> dict:
        now = datetime.now(timezone.utc).isoformat()
        return {
            "event_type": "actor.state.updated",
            "timestamp_utc": now,
            "source": "load_gen",
            "trace_id": str(uuid.uuid4()),
            "payload": {
                "vehicle_id": self.vid,
                "timestamp_utc": now,
                "position": {"lat": self.lat, "lon": self.lon, "uncertainty_m": 2.0},
                "speed_mps": round(self.speed, 3),
                "heading_deg": round(self.heading, 2),
                "vehicle_type": self.vtype,
                "source": "load_gen",
                "scenario_run_id": "load-gen",
            },
        }


async def _run_synthetic(
    url: str,
    vehicles: int,
    hz: float,
    duration: float,
    seed: int,
    quiet: bool,
) -> None:
    rng = random.Random(seed)
    fleet = [
        _Vehicle(f"load-{i:04d}", rng.choice(_VEHICLE_TYPES), rng)
        for i in range(vehicles)
    ]
    dt = 1.0 / hz
    ingest_url = f"{url}/v1/world-state/ingest"
    ticks = int(duration * hz)

    sent = errors = 0
    t_start = time.monotonic()

    async with httpx.AsyncClient(timeout=5.0) as client:
        for tick in range(ticks):
            tick_start = time.monotonic()

            for v in fleet:
                v.step(dt, rng)

            events = [v.to_event() for v in fleet]
            try:
                r = await client.post(ingest_url, json={"events": events})
                r.raise_for_status()
                sent += len(events)
            except Exception as exc:
                errors += 1
                if not quiet:
                    print(f"[tick {tick}] POST failed: {exc}", file=sys.stderr)

            if not quiet and tick % max(1, int(hz)) == 0:
                elapsed = time.monotonic() - t_start
                rate = sent / elapsed if elapsed > 0 else 0
                print(
                    f"  t={elapsed:6.1f}s  tick={tick:5d}/{ticks}  "
                    f"events_sent={sent:7d}  errors={errors}  rate={rate:.0f}/s"
                )

            sleep = max(0.0, dt - (time.monotonic() - tick_start))
            await asyncio.sleep(sleep)

    elapsed = time.monotonic() - t_start
    rate = sent / elapsed if elapsed > 0 else 0
    print(
        f"\nDone. vehicles={vehicles}  hz={hz}  duration={elapsed:.1f}s\n"
        f"  events_sent={sent}  errors={errors}  avg_rate={rate:.1f} events/s"
    )


async def _run_fixture(url: str, fixture_path: Path, speed: float, quiet: bool) -> None:
    """Replay a scenario fixture file by synthesising events from actor definitions."""
    data = json.loads(fixture_path.read_text())
    name = data.get("name", fixture_path.stem)
    duration_s = float(data.get("duration_s", 300.0))
    seed = int(data.get("seed", 42))
    tc = data.get("traffic_composition", {})

    # Build vehicle count from fractions (base pool 40)
    fractions = [
        ("car", tc.get("car_fraction", 0.6)),
        ("truck", tc.get("truck_fraction", 0.1)),
        ("bus", tc.get("bus_fraction", 0.05)),
        ("motorcycle", tc.get("motorcycle_fraction", 0.15)),
        ("auto_rickshaw", tc.get("auto_rickshaw_fraction", 0.08)),
        ("bicycle", tc.get("bicycle_fraction", 0.02)),
    ]
    rng = random.Random(seed)
    fleet: list[_Vehicle] = []
    for vtype, frac in fractions:
        count = max(1, round(40 * frac))
        for i in range(count):
            fleet.append(_Vehicle(f"{vtype}-{i:04d}", vtype, rng))

    print(f"Replaying fixture '{name}': {len(fleet)} vehicles, {duration_s}s at {speed}x speed")
    await _run_synthetic(
        url=url,
        vehicles=len(fleet),
        hz=10.0,
        duration=duration_s / speed,
        seed=seed,
        quiet=quiet,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Marga load generator — stress the world-state ingest endpoint",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--url", default="http://localhost:8000", help="Gateway base URL")
    parser.add_argument("--vehicles", type=int, default=50, help="Number of synthetic vehicles")
    parser.add_argument("--hz", type=float, default=10.0, help="Tick rate (updates/second)")
    parser.add_argument("--duration", type=float, default=30.0, help="Test duration in seconds")
    parser.add_argument("--seed", type=int, default=42, help="RNG seed for reproducibility")
    parser.add_argument("--fixture", type=Path, default=None, help="Replay a scenario fixture JSON file")
    parser.add_argument("--speed", type=float, default=1.0, help="Fixture replay speed multiplier")
    parser.add_argument("--quiet", action="store_true", help="Suppress per-tick output")
    args = parser.parse_args()

    if args.fixture:
        asyncio.run(_run_fixture(args.url, args.fixture, args.speed, args.quiet))
    else:
        asyncio.run(
            _run_synthetic(
                url=args.url,
                vehicles=args.vehicles,
                hz=args.hz,
                duration=args.duration,
                seed=args.seed,
                quiet=args.quiet,
            )
        )


if __name__ == "__main__":
    main()
