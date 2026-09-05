"""Normalize adapter event envelopes into the shared Marga canonical model."""

from __future__ import annotations

import math
from collections.abc import Iterable
from typing import Any

from packages.schemas.canonical import ActorType, PedestrianState, SourceType, VehicleState


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres between two WGS84 points."""
    r = 6_371_000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return 2 * r * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def actor_within_range(
    actor_lat: float,
    actor_lon: float,
    source_lat: float,
    source_lon: float,
    range_m: float,
) -> bool:
    """Return True if the actor is within range_m of the reporting RSU/source."""
    return _haversine_m(actor_lat, actor_lon, source_lat, source_lon) <= range_m

_ACTOR_TYPES = {
    "car": ActorType.CAR,
    "truck": ActorType.TRUCK,
    "bus": ActorType.BUS,
    "motorcycle": ActorType.BIKE,
    "bicycle": ActorType.BIKE,
    "auto_rickshaw": ActorType.AUTO,
    "tractor": ActorType.OTHER,
    "emergency": ActorType.AMBULANCE,
}


def _as_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump(mode="python")
        if isinstance(dumped, dict):
            return dumped
    raise TypeError("adapter event must be a mapping or Pydantic model")


def vehicle_from_adapter_event(event: Any) -> VehicleState:
    """Convert a simulation/real-shaped actor event into ``VehicleState``.

    The bridge accepts only generic event and payload fields. SUMO concepts are
    intentionally confined to the adapter that produced the event.
    """
    envelope = _as_mapping(event)
    if envelope.get("event_type") != "actor.state.updated":
        raise ValueError("expected actor.state.updated event")
    payload = _as_mapping(envelope.get("payload"))
    actor_id = payload.get("vehicle_id") or payload.get("actor_id")
    if not isinstance(actor_id, str) or not actor_id:
        raise ValueError("actor state payload requires vehicle_id or actor_id")
    position = _as_mapping(payload.get("position"))
    vehicle_type = str(payload.get("vehicle_type", payload.get("actor_type", "car"))).lower()
    actor_type = _ACTOR_TYPES.get(vehicle_type, ActorType.OTHER)
    return VehicleState.model_validate(
        {
            "actor_id": actor_id,
            "actor_type": actor_type,
            "ts": payload.get("timestamp_utc", payload.get("ts", envelope.get("timestamp_utc"))),
            "position": {
                "lat": position["lat"],
                "lon": position["lon"],
                "altitude_m": position.get("alt_m", position.get("altitude_m")),
            },
            "position_uncertainty_m": position.get("uncertainty_m", payload.get("position_uncertainty_m", 0.0)),
            "speed_mps": payload["speed_mps"],
            "acceleration_mps2": payload.get("acceleration_mps2"),
            "heading_deg": float(payload["heading_deg"]) % 360,
            "road_segment_id": payload.get("road_segment_id"),
            "lane_id": payload.get("lane_id"),
            "source": SourceType.SIMULATION,
            "capabilities": list(payload.get("capabilities", [])),
        }
    )


def is_pedestrian_adapter_event(event: Any) -> bool:
    """Return whether a generic actor envelope represents a pedestrian."""
    envelope = _as_mapping(event)
    payload = _as_mapping(envelope.get("payload"))
    return "pedestrian_id" in payload or str(payload.get("actor_type", "")).upper() == "PEDESTRIAN"


def pedestrian_from_adapter_event(event: Any) -> PedestrianState:
    """Convert a generic pedestrian adapter envelope into canonical state."""
    envelope = _as_mapping(event)
    if envelope.get("event_type") != "actor.state.updated":
        raise ValueError("expected actor.state.updated event")
    payload = _as_mapping(envelope.get("payload"))
    actor_id = payload.get("pedestrian_id") or payload.get("actor_id")
    if not isinstance(actor_id, str) or not actor_id:
        raise ValueError("pedestrian state payload requires pedestrian_id or actor_id")
    position = _as_mapping(payload.get("position"))
    return PedestrianState.model_validate(
        {
            "actor_id": actor_id,
            "ts": payload.get("timestamp_utc", payload.get("ts", envelope.get("timestamp_utc"))),
            "position": {
                "lat": position["lat"],
                "lon": position["lon"],
                "altitude_m": position.get("alt_m", position.get("altitude_m")),
            },
            "position_uncertainty_m": position.get("uncertainty_m", payload.get("position_uncertainty_m", 0.0)),
            "speed_mps": payload["speed_mps"],
            "heading_deg": float(payload["heading_deg"]) % 360,
            "road_segment_id": payload.get("road_segment_id"),
            "source": SourceType.SIMULATION,
        }
    )


def world_state_from_adapter_events(
    events: Iterable[Any],
) -> dict[str, list[VehicleState] | list[PedestrianState]]:
    """Build the detector-facing current world snapshot from actor events."""
    vehicles: list[VehicleState] = []
    pedestrians: list[PedestrianState] = []
    for event in events:
        envelope = _as_mapping(event)
        if envelope.get("event_type") != "actor.state.updated":
            continue
        if is_pedestrian_adapter_event(envelope):
            pedestrians.append(pedestrian_from_adapter_event(envelope))
        else:
            vehicles.append(vehicle_from_adapter_event(envelope))
    return {"vehicles": vehicles, "pedestrians": pedestrians}
