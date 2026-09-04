"""Reusable test scenario builders.

Every builder returns a world_state dict suitable for feeding to
detectors. No hard-coded coordinates or actor IDs - all values are
parameterised or generated.

All detectors expect road_network.segments as a *list* of dicts, each
containing a ``segment_id`` key.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from packages.geo.helpers import point_along_bearing
from packages.schemas.canonical import (
    ActorType,
    Position,
    SourceType,
    VehicleState,
)


def _uid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _seg(
    segment_id: str,
    direction_deg: float,
    road_type: str = "URBAN",
    connected_segments: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "segment_id": segment_id,
        "direction_deg": direction_deg,
        "type": road_type,
        "connected_segments": connected_segments or [],
    }


# ---------------------------------------------------------------------------
# Wrong-way scenarios
# ---------------------------------------------------------------------------

def create_wrong_way_scenario(
    road_direction_deg: float = 0.0,
    vehicle_heading_deg: float = 180.0,
    speed_mps: float = 15.0,
    num_updates: int = 5,
    road_type: str = "URBAN",
) -> dict[str, Any]:
    """Scenario where a vehicle drives against road direction for N updates."""
    seg_id = _uid()
    actor_id = _uid()
    base_lat, base_lon = 12.9716, 77.5946

    vehicles_sequence: list[list[VehicleState]] = []
    for i in range(num_updates):
        lat, lon = point_along_bearing(
            base_lat, base_lon, vehicle_heading_deg, speed_mps * i,
        )
        vs = VehicleState(
            actor_id=actor_id,
            ts=_now() + timedelta(seconds=i),
            position=Position(lat=lat, lon=lon),
            position_uncertainty_m=2.0,
            speed_mps=speed_mps,
            heading_deg=vehicle_heading_deg % 360,
            road_segment_id=seg_id,
        )
        vehicles_sequence.append([vs])

    return {
        "vehicles_sequence": vehicles_sequence,
        "road_network": {
            "segments": [_seg(seg_id, road_direction_deg, road_type=road_type)],
        },
        "hazards": [],
        "observations": [],
        "signals": {},
        "metadata": {
            "actor_id": actor_id,
            "segment_id": seg_id,
        },
    }


# ---------------------------------------------------------------------------
# Emergency braking scenarios
# ---------------------------------------------------------------------------

def create_braking_scenario(
    initial_speed_mps: float = 20.0,
    deceleration_mps2: float = -6.0,
    follower_distance_m: float = 50.0,
) -> dict[str, Any]:
    """Scenario with a braking vehicle and a follower."""
    seg_id = _uid()
    braker_id = _uid()
    follower_id = _uid()
    base_lat, base_lon = 12.9716, 77.5946

    dt = 0.5
    braker_updates: list[list[VehicleState]] = []
    for i in range(6):
        t = i * dt
        speed = max(0.0, initial_speed_mps + deceleration_mps2 * t)
        lat, lon = point_along_bearing(base_lat, base_lon, 0.0, initial_speed_mps * t)
        follower_lat, follower_lon = point_along_bearing(
            base_lat, base_lon, 180.0, follower_distance_m,
        )
        braker = VehicleState(
            actor_id=braker_id,
            ts=_now() + timedelta(seconds=t),
            position=Position(lat=lat, lon=lon),
            position_uncertainty_m=2.0,
            speed_mps=speed,
            acceleration_mps2=deceleration_mps2 if speed > 0 else 0.0,
            heading_deg=0.0,
            road_segment_id=seg_id,
        )
        follower = VehicleState(
            actor_id=follower_id,
            ts=_now() + timedelta(seconds=t),
            position=Position(lat=follower_lat, lon=follower_lon),
            position_uncertainty_m=2.0,
            speed_mps=initial_speed_mps,
            heading_deg=0.0,
            road_segment_id=seg_id,
        )
        braker_updates.append([braker, follower])

    return {
        "vehicles_sequence": braker_updates,
        "road_network": {
            "segments": [_seg(seg_id, 0.0)],
        },
        "hazards": [],
        "observations": [],
        "signals": {},
        "metadata": {
            "braker_id": braker_id,
            "follower_id": follower_id,
            "segment_id": seg_id,
        },
    }


# ---------------------------------------------------------------------------
# Stalled vehicle scenarios
# ---------------------------------------------------------------------------

def create_stalled_scenario(
    stopped_duration_s: float = 60.0,
    surrounding_flow_mps: float = 10.0,
    lane_type: str = "travel",
) -> dict[str, Any]:
    """Scenario with a stopped vehicle surrounded by flowing traffic."""
    seg_id = _uid()
    stalled_id = _uid()
    base_lat, base_lon = 12.9716, 77.5946

    flowing_vehicles = []
    for i in range(3):
        offset_lat, offset_lon = point_along_bearing(
            base_lat, base_lon, 0.0, 30.0 * (i + 1),
        )
        flowing_vehicles.append(
            VehicleState(
                actor_id=_uid(),
                ts=_now(),
                position=Position(lat=offset_lat, lon=offset_lon),
                position_uncertainty_m=2.0,
                speed_mps=surrounding_flow_mps,
                heading_deg=0.0,
                road_segment_id=seg_id,
                lane_id="lane-1" if lane_type == "travel" else "shoulder-1",
            )
        )

    lane_id = "lane-1" if lane_type == "travel" else "shoulder-1"
    stalled_vehicle = VehicleState(
        actor_id=stalled_id,
        ts=_now(),
        position=Position(lat=base_lat, lon=base_lon),
        position_uncertainty_m=2.0,
        speed_mps=0.0,
        heading_deg=0.0,
        road_segment_id=seg_id,
        lane_id=lane_id,
    )

    return {
        "vehicles": [stalled_vehicle] + flowing_vehicles,
        "road_network": {
            "segments": [_seg(seg_id, 0.0)],
        },
        "hazards": [],
        "observations": [],
        "signals": {},
        "metadata": {
            "stalled_id": stalled_id,
            "segment_id": seg_id,
            "stopped_duration_s": stopped_duration_s,
        },
    }


# ---------------------------------------------------------------------------
# Blind intersection scenarios
# ---------------------------------------------------------------------------

def create_blind_intersection_scenario(
    approach_speeds: tuple[float, float] = (10.0, 10.0),
    approach_distances: tuple[float, float] = (50.0, 50.0),
    signal_state: str | None = None,
) -> dict[str, Any]:
    """Scenario with vehicles approaching an intersection from perpendicular roads.

    Uses the dict-format expected by BlindIntersectionDetector (vehicles as dicts,
    intersections as a top-level list with conflict_zones).
    """
    int_id = _uid()
    seg_north = _uid()
    seg_east = _uid()
    int_lat, int_lon = 12.9716, 77.5946

    v1_lat, v1_lon = point_along_bearing(int_lat, int_lon, 180.0, approach_distances[0])
    v2_lat, v2_lon = point_along_bearing(int_lat, int_lon, 270.0, approach_distances[1])

    v1_id = _uid()
    v2_id = _uid()

    v1 = {
        "actor_id": v1_id,
        "position": {"lat": v1_lat, "lon": v1_lon},
        "position_uncertainty_m": 10.0,
        "speed_mps": approach_speeds[0],
        "heading_deg": 0.0,
        "road_segment_id": seg_north,
    }
    v2 = {
        "actor_id": v2_id,
        "position": {"lat": v2_lat, "lon": v2_lon},
        "position_uncertainty_m": 10.0,
        "speed_mps": approach_speeds[1],
        "heading_deg": 90.0,
        "road_segment_id": seg_east,
    }

    sig = None
    if signal_state:
        sig = {"movements": {seg_north: signal_state, seg_east: "RED"}}

    intersection = {
        "intersection_id": int_id,
        "position": {"lat": int_lat, "lon": int_lon},
        "conflict_zones": [
            {
                "zone_id": f"cz-{int_id}",
                "approaching_segments": [seg_north, seg_east],
                "geometry": {
                    "type": "Point",
                    "coordinates": [int_lon, int_lat],
                },
            },
        ],
        "signal_state": sig,
    }

    return {
        "vehicles": [v1, v2],
        "intersections": [intersection],
        "road_network": {
            "segments": [
                _seg(seg_north, 0.0),
                _seg(seg_east, 90.0),
            ],
        },
        "hazards": [],
        "signals": {},
        "metadata": {
            "intersection_id": int_id,
            "v1_id": v1_id,
            "v2_id": v2_id,
            "seg_north": seg_north,
            "seg_east": seg_east,
        },
    }


# ---------------------------------------------------------------------------
# Animal crossing scenarios
# ---------------------------------------------------------------------------

def create_animal_crossing_scenario(
    animal_class: str = "cow",
    animal_speed: float = 2.0,
    road_distance_m: float = 10.0,
    heading_toward_road: bool = True,
) -> dict[str, Any]:
    """Scenario with an animal near a road.

    Uses dict-format expected by AnimalConflictDetector (dynamic_actors and
    vehicles as plain dicts).
    """
    seg_id = _uid()
    road_lat, road_lon = 12.9716, 77.5946

    if heading_toward_road:
        animal_lat, animal_lon = point_along_bearing(
            road_lat, road_lon, 270.0, road_distance_m,
        )
        animal_heading = 90.0
    else:
        animal_lat, animal_lon = point_along_bearing(
            road_lat, road_lon, 270.0, road_distance_m,
        )
        animal_heading = 0.0

    now = _now()
    track_id = _uid()

    obs = {
        "observation_id": _uid(),
        "track_id": track_id,
        "actor_class": animal_class,
        "ts": now,
        "position": {"lat": animal_lat, "lon": animal_lon},
        "speed_mps": animal_speed,
        "heading_deg": animal_heading,
        "detector_confidence": 0.8,
        "source_id": _uid(),
    }

    vehicle_lat, vehicle_lon = point_along_bearing(road_lat, road_lon, 180.0, 40.0)
    vehicle_id = _uid()
    vehicle = {
        "actor_id": vehicle_id,
        "position": {"lat": vehicle_lat, "lon": vehicle_lon},
        "position_uncertainty_m": 2.0,
        "speed_mps": 15.0,
        "heading_deg": 0.0,
        "road_segment_id": seg_id,
    }

    return {
        "vehicles": [vehicle],
        "dynamic_actors": [obs],
        "road_network": {
            "segments": [
                {
                    "segment_id": seg_id,
                    "direction_deg": 0.0,
                    "type": "URBAN",
                },
            ],
        },
        "hazards": [],
        "signals": {},
        "metadata": {
            "track_id": track_id,
            "vehicle_id": vehicle_id,
            "segment_id": seg_id,
        },
    }


# ---------------------------------------------------------------------------
# Emergency vehicle scenarios
# ---------------------------------------------------------------------------

def create_emergency_vehicle_scenario(
    is_verified: bool = True,
    corridor_match: bool = True,
) -> dict[str, Any]:
    """Scenario with an emergency vehicle and nearby traffic."""
    seg_id = _uid()
    ev_id = _uid()
    base_lat, base_lon = 12.9716, 77.5946

    ev = VehicleState(
        actor_id=ev_id,
        actor_type=ActorType.AMBULANCE,
        ts=_now(),
        position=Position(lat=base_lat, lon=base_lon),
        position_uncertainty_m=2.0,
        speed_mps=20.0,
        heading_deg=0.0,
        road_segment_id=seg_id,
    )

    other_seg = seg_id if corridor_match else _uid()
    other_lat, other_lon = point_along_bearing(base_lat, base_lon, 0.0, 200.0)
    other = VehicleState(
        actor_id=_uid(),
        ts=_now(),
        position=Position(lat=other_lat, lon=other_lon),
        position_uncertainty_m=2.0,
        speed_mps=10.0,
        heading_deg=0.0,
        road_segment_id=other_seg,
    )

    verified_ids = {ev_id} if is_verified else set()

    return {
        "vehicles": [ev, other],
        "road_network": {
            "segments": [_seg(seg_id, 0.0)],
        },
        "hazards": [],
        "observations": [],
        "signals": {},
        "verified_emergency_ids": verified_ids,
        "metadata": {
            "ev_id": ev_id,
            "other_id": other.actor_id,
            "segment_id": seg_id,
        },
    }
