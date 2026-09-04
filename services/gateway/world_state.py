"""In-memory world-state store with REST snapshot and WebSocket streaming."""

from __future__ import annotations

import asyncio
import logging
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
