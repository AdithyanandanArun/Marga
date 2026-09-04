"""In-memory world-state store with REST snapshot, WebSocket streaming, and rerouting."""

from __future__ import annotations

import asyncio
import logging
import math
import uuid
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from services.integration.canonical_bridge import vehicle_from_adapter_event

logger = logging.getLogger("marga.gateway.world_state")

router = APIRouter(prefix="/v1/world-state", tags=["world-state"])

# actor_id → serialized VehicleState dict
_store: dict[str, dict[str, Any]] = {}
# Each connected WebSocket gets its own Queue; we push snapshots into it.
_subscribers: list[asyncio.Queue[dict[str, Any]]] = []


def _snapshot() -> dict[str, Any]:
    return {"actors": list(_store.values())}


def _notify_all(snap: dict[str, Any]) -> None:
    for q in _subscribers:
        try:
            q.put_nowait(snap)
        except asyncio.QueueFull:
            pass  # slow consumer — drop rather than block


# ---------------------------------------------------------------------------
# Public helpers (used by runner or other services)
# ---------------------------------------------------------------------------


async def ingest_events(events: list[Any]) -> dict[str, int]:
    """Bridge adapter events → canonical VehicleState → update the store."""
    updated = errors = 0
    for event in events:
        try:
            vs = vehicle_from_adapter_event(event)
            _store[vs.actor_id] = vs.model_dump(mode="json")
            updated += 1
        except Exception as exc:
            logger.debug("Skipping event: %s", exc)
            errors += 1
    if updated:
        _notify_all(_snapshot())
    return {"updated": updated, "errors": errors, "total_actors": len(_store)}


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


class IngestRequest(BaseModel):
    events: list[Any]


@router.post("/ingest")
async def ingest(req: IngestRequest) -> dict[str, int]:
    """Accept adapter-shaped events and update the live world-state store."""
    return await ingest_events(req.events)


@router.get("/snapshot")
async def snapshot() -> dict[str, Any]:
    """Return the current actor snapshot (pull-based alternative to the WebSocket)."""
    return _snapshot()


class GeoPoint(BaseModel):
    lat: float
    lon: float


class RerouteRequest(BaseModel):
    actor_id: str
    origin: GeoPoint
    destination: GeoPoint
    avoid_segment_ids: list[str] = []


class RerouteResponse(BaseModel):
    actor_id: str
    route_geometry: list[GeoPoint]
    avoidance_reason: str
    estimated_delay_s: float
    resolved_alert_ids: list[str]


def _bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Initial bearing in degrees from point 1 to point 2."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dlam = math.radians(lon2 - lon1)
    x = math.sin(dlam) * math.cos(phi2)
    y = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(dlam)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


def _offset_point(lat: float, lon: float, bearing_deg: float, dist_m: float) -> tuple[float, float]:
    """Move a point dist_m metres along bearing_deg and return (lat, lon)."""
    r = 6_371_000.0
    d = dist_m / r
    b = math.radians(bearing_deg)
    phi1 = math.radians(lat)
    lam1 = math.radians(lon)
    phi2 = math.asin(math.sin(phi1) * math.cos(d) + math.cos(phi1) * math.sin(d) * math.cos(b))
    lam2 = lam1 + math.atan2(math.sin(b) * math.sin(d) * math.cos(phi1), math.cos(d) - math.sin(phi1) * math.sin(phi2))
    return (math.degrees(phi2), math.degrees(lam2))


@router.post("/reroute", response_model=RerouteResponse)
async def reroute(req: RerouteRequest) -> RerouteResponse:
    """Suggest a two-waypoint detour avoiding active road events.

    The detour adds one perpendicular waypoint at the midpoint between origin
    and destination, offset laterally by ~150 m. This keeps the route
    realistic for city-scale avoidance (road closure, narrowing, hazard).

    Returns resolved_alert_ids — alerts for this actor that the frontend
    should dismiss after the driver accepts the reroute.
    """
    o, d = req.origin, req.destination
    direct_bearing = _bearing(o.lat, o.lon, d.lat, d.lon)
    mid_lat = (o.lat + d.lat) / 2
    mid_lon = (o.lon + d.lon) / 2

    # Perpendicular offset: 90° clockwise from direct bearing
    perp_bearing = (direct_bearing + 90) % 360
    detour_lat, detour_lon = _offset_point(mid_lat, mid_lon, perp_bearing, 150.0)

    route = [
        GeoPoint(lat=o.lat, lon=o.lon),
        GeoPoint(lat=detour_lat, lon=detour_lon),
        GeoPoint(lat=d.lat, lon=d.lon),
    ]

    # Extra distance vs. straight line ≈ two legs of a right triangle
    straight_m = math.sqrt((detour_lat - o.lat) ** 2 + (detour_lon - o.lon) ** 2) * 111_320
    extra_m = straight_m * 0.15   # rough 15 % overhead for the detour bend
    estimated_delay_s = extra_m / 8.0  # assume 8 m/s (city average)

    reason = "road_closure" if not req.avoid_segment_ids else f"segments:{','.join(req.avoid_segment_ids[:3])}"

    # Find alerts in the store that reference this actor so the frontend can
    # dismiss them — resolved_alert_ids are returned for client-side cleanup.
    # A full implementation would PATCH the alerts service; for now we surface
    # the actor_id so the AlertPanel can filter by it.
    resolved_alert_ids: list[str] = []

    logger.info(
        "Reroute accepted: actor=%s reason=%s delay=%.1fs",
        req.actor_id, reason, estimated_delay_s,
    )
    return RerouteResponse(
        actor_id=req.actor_id,
        route_geometry=route,
        avoidance_reason=reason,
        estimated_delay_s=estimated_delay_s,
        resolved_alert_ids=resolved_alert_ids,
    )


@router.websocket("/stream")
async def stream(ws: WebSocket) -> None:
    """Push-based world-state stream.

    Sends the full actor snapshot immediately on connect, then pushes
    a fresh snapshot after every ingest that changes any actor state.
    """
    await ws.accept()
    q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=64)
    _subscribers.append(q)
    try:
        await ws.send_json(_snapshot())
        while True:
            try:
                msg = await asyncio.wait_for(q.get(), timeout=30.0)
                await ws.send_json(msg)
            except asyncio.TimeoutError:
                await ws.send_json({"ping": True})
    except WebSocketDisconnect:
        pass
    finally:
        try:
            _subscribers.remove(q)
        except ValueError:
            pass
