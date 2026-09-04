"""FastAPI router for the Alert Platform service.

Exposes REST endpoints for alert CRUD and a WebSocket stream that
pushes lifecycle events (issued, updated, cleared) to subscribed
clients with optional bounding-box filtering.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, WebSocket, WebSocketDisconnect
from marga_schemas.alert import Alert, AlertPriority, AlertState
from pydantic import BaseModel, Field

from .lifecycle import AlertLifecycleManager
from .prioritizer import AlertPrioritizer
from .store import AlertStore

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Shared singletons (in a production deployment these would be injected via
# FastAPI dependency overrides or a DI container).
# ---------------------------------------------------------------------------
store = AlertStore()
lifecycle = AlertLifecycleManager()
prioritizer = AlertPrioritizer()

router = APIRouter(prefix="/v1", tags=["alerts"])

# ---------------------------------------------------------------------------
# WebSocket client registry
# ---------------------------------------------------------------------------


class _WsClient:
    """Thin wrapper around a WebSocket with optional bbox filter."""

    __slots__ = ("bbox", "ws")

    def __init__(self, ws: WebSocket, bbox: tuple[float, float, float, float] | None = None):
        self.ws = ws
        self.bbox = bbox


_clients: set[_WsClient] = set()
_clients_lock = asyncio.Lock()


async def _broadcast(event_type: str, alert: Alert) -> None:
    """Push an event to all connected WebSocket clients."""
    payload = {
        "event": event_type,
        "alert": json.loads(alert.model_dump_json()),
        "ts": datetime.now(UTC).isoformat(),
    }
    async with _clients_lock:
        dead: list[_WsClient] = []
        for client in _clients:
            # Apply bbox filter if the client specified one.
            if client.bbox is not None:
                pos = alert.position
                if pos is None or not _in_bbox(pos.lat, pos.lon, client.bbox):
                    continue
            try:
                await client.ws.send_json(payload)
            except Exception:
                dead.append(client)
        for d in dead:
            _clients.discard(d)


def _in_bbox(lat: float, lon: float, bbox: tuple[float, float, float, float]) -> bool:
    min_lat, min_lon, max_lat, max_lon = bbox
    return min_lat <= lat <= max_lat and min_lon <= lon <= max_lon


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class AlertCreateRequest(BaseModel):
    alert_type: str
    priority: AlertPriority
    title: str
    description: str
    confidence: float = Field(ge=0, le=1)
    position: dict[str, float] | None = None
    affected_actor_ids: list[str] = Field(default_factory=list)
    risk_id: str | None = None
    hazard_id: str | None = None
    ttl_s: int | None = None
    machine_reasoning: dict[str, Any] = Field(default_factory=dict)
    driver_text: str | None = None


class AlertPatchRequest(BaseModel):
    state: AlertState | None = None
    resolution_reason: str | None = None


class HealthResponse(BaseModel):
    status: str = "ok"
    active_alerts: int = 0


# ---------------------------------------------------------------------------
# REST endpoints
# ---------------------------------------------------------------------------


@router.get("/alerts/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(active_alerts=store.get_active_count())


@router.get("/alerts")
async def list_alerts(
    bbox: str | None = Query(None, description="min_lat,min_lon,max_lat,max_lon"),
    alert_type: str | None = Query(None),
    priority: str | None = Query(None),
    state: str | None = Query(None),
    actor_id: str | None = Query(None),
    limit: int = Query(100, ge=1, le=1000),
) -> list[dict[str, Any]]:
    parsed_bbox: tuple[float, float, float, float] | None = None
    if bbox is not None:
        parts = [float(p) for p in bbox.split(",")]
        if len(parts) != 4:
            raise HTTPException(400, "bbox must be min_lat,min_lon,max_lat,max_lon")
        parsed_bbox = (parts[0], parts[1], parts[2], parts[3])

    alerts = store.query(
        bbox=parsed_bbox,
        alert_type=alert_type,
        priority=priority,
        state=state,
        actor_id=actor_id,
        limit=limit,
    )
    # Sort by priority score descending for the response.
    alerts = prioritizer.prioritize(alerts)
    return [json.loads(a.model_dump_json()) for a in alerts]


@router.get("/alerts/{alert_id}")
async def get_alert(alert_id: UUID) -> dict[str, Any]:
    alert = store.get(alert_id)
    if alert is None:
        raise HTTPException(404, "Alert not found")
    return json.loads(alert.model_dump_json())


@router.post("/alerts", status_code=201)
async def create_alert(req: AlertCreateRequest) -> dict[str, Any]:
    from marga_schemas.common import GeoPoint

    pos = GeoPoint(**req.position) if req.position else None
    risk_id = UUID(req.risk_id) if req.risk_id else None
    hazard_id = UUID(req.hazard_id) if req.hazard_id else None

    alert = Alert(
        alert_type=req.alert_type,
        priority=req.priority,
        title=req.title,
        description=req.description,
        confidence=req.confidence,
        position=pos,
        affected_actor_ids=req.affected_actor_ids,
        risk_id=risk_id,
        hazard_id=hazard_id,
        ttl_s=req.ttl_s,
        machine_reasoning=req.machine_reasoning,
        driver_text=req.driver_text,
    )

    alert = lifecycle.create_alert(alert)
    store.add(alert)

    if alert.state == AlertState.ACTIVE:
        await _broadcast("alert.issued", alert)
        # NATS (obj 2.1)
        try:
            from packages.event_bus.bus import get_event_bus
            bus = get_event_bus()
            if bus and bus.connected:
                await bus.publish("alert.issued", json.loads(alert.model_dump_json()))
        except Exception:
            pass

    return json.loads(alert.model_dump_json())


@router.patch("/alerts/{alert_id}")
async def patch_alert(alert_id: UUID, req: AlertPatchRequest) -> dict[str, Any]:
    existing = store.get(alert_id)
    if existing is None:
        raise HTTPException(404, "Alert not found")

    try:
        if req.state == AlertState.RESOLVED and req.resolution_reason:
            alert = lifecycle.resolve_alert(alert_id, req.resolution_reason)
        elif req.state is not None:
            alert = lifecycle.update_alert(alert_id, {"state": req.state})
        else:
            raise HTTPException(400, "No state change provided")
    except KeyError as exc:
        raise HTTPException(404, "Alert not found") from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc

    store.update(alert)

    event_type = "alert.cleared" if alert.state in {AlertState.RESOLVED, AlertState.EXPIRED} else "alert.updated"
    await _broadcast(event_type, alert)
    # NATS (obj 2.1)
    try:
        from packages.event_bus.bus import get_event_bus
        bus = get_event_bus()
        if bus and bus.connected:
            await bus.publish(event_type, json.loads(alert.model_dump_json()))
    except Exception:
        pass

    return json.loads(alert.model_dump_json())


# ---------------------------------------------------------------------------
# WebSocket stream
# ---------------------------------------------------------------------------


@router.websocket("/stream/alerts")
async def stream_alerts(
    ws: WebSocket,
    bbox: str | None = Query(None),
) -> None:
    await ws.accept()

    parsed_bbox: tuple[float, float, float, float] | None = None
    if bbox is not None:
        try:
            parts = [float(p) for p in bbox.split(",")]
            if len(parts) == 4:
                parsed_bbox = (parts[0], parts[1], parts[2], parts[3])
        except ValueError:
            pass

    client = _WsClient(ws, parsed_bbox)
    async with _clients_lock:
        _clients.add(client)
    # Metrics (obj 2.5)
    try:
        from marga_observability.metrics import metrics as _m
        _m.websocket_clients.inc()
    except Exception:
        pass

    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        async with _clients_lock:
            _clients.discard(client)
        try:
            from marga_observability.metrics import metrics as _m
            _m.websocket_clients.dec()
        except Exception:
            pass
