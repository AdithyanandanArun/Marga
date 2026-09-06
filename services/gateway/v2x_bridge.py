"""Gateway bridge from canonical actor ingestion to local Edge V2X."""

from __future__ import annotations

import asyncio
import time
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from marga_schemas.messaging import V2XMessage

from packages.schemas.canonical import RiskEvent, VehicleState
from services.edge_v2x.manager import EdgeV2XManager

router = APIRouter(prefix="/v1", tags=["edge-v2x"])
manager = EdgeV2XManager()
_subscribers: list[asyncio.Queue[dict[str, Any]]] = []
_started = False
_last_observation_at: dict[str, float] = {}
_MIN_OBSERVATION_INTERVAL_S = 0.35


async def initialize() -> None:
    global _started
    if _started:
        return
    manager.on_message(_on_message)
    manager.on_risk(_on_risk)
    _started = True


async def shutdown() -> None:
    global _started
    await manager.shutdown()
    _last_observation_at.clear()
    _started = False


async def observe_actor(state: VehicleState) -> None:
    await initialize()
    now = time.monotonic()
    # PC5 safety needs fresh local state, not every UI animation frame.  The
    # manager's peer refresh is O(n²), so coalescing sub-350ms repeats keeps
    # the live Control Center responsive while bounding state age.
    if now - _last_observation_at.get(state.actor_id, float("-inf")) < _MIN_OBSERVATION_INTERVAL_S:
        return
    _last_observation_at[state.actor_id] = now
    await manager.update_actor_state(state)
    link = manager.get_connectivity(state.actor_id)
    if link is not None:
        _broadcast({"type": "node.connectivity", "connectivity": {
            "node_id": state.actor_id,
            "transport_state": _transport_state(str(link["connectivity"])),
            "link_quality": 1.0 if link["direct_peers"] else 0.0,
            "cloud_reachable": link["cloud_reachable"],
            "pc5_active": link["pc5_active"],
        }})


async def retire_actor(actor_id: str) -> None:
    """Remove departed actors from the radio mesh as well as the world."""
    _last_observation_at.pop(actor_id, None)
    await manager.remove_node(actor_id)


def _transport_state(connectivity: str) -> str:
    if connectivity == "FULL":
        return "CONNECTED"
    if connectivity in {"DIRECT_ONLY", "INTERMITTENT"}:
        return "DEGRADED"
    return "DISCONNECTED"


def _broadcast(event: dict[str, Any]) -> None:
    for queue in tuple(_subscribers):
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            pass


async def _on_message(message: V2XMessage) -> None:
    _broadcast({"type": "v2x.message", "message": {
        "message_id": str(message.message_id), "from_node_id": message.sender_id,
        "priority": message.priority.value, "payload": message.payload,
        "sent_at": message.timestamp.isoformat(), "transport": "PC5",
    }})


async def _on_risk(risk: RiskEvent, node_id: str) -> None:
    evidence = next((item for item in risk.evidence if "max_vulnerability" in item), {})
    _broadcast({"type": "risk.created", "risk": {
        "risk_id": risk.risk_id, "conflict_type": risk.type.value,
        "collision_probability": risk.risk_score, "ttc_s": risk.time_to_conflict_s,
        "uncertainty": 1 - risk.confidence, "consequence": risk.severity,
        "vulnerability": evidence.get("max_vulnerability", 0.4),
        "priority_score": risk.risk_score, "affected_node_ids": risk.affected_actor_ids,
        "created_at": risk.ts.isoformat(), "node_id": node_id,
    }})


@router.get("/nodes/{actor_id}/neighbours")
async def neighbours(actor_id: str) -> list[dict[str, Any]]:
    return manager.get_neighbours(actor_id) or []


@router.get("/nodes/{actor_id}/connectivity")
async def connectivity(actor_id: str) -> dict[str, Any]:
    return manager.get_connectivity(actor_id) or {"node_id": actor_id, "pc5_active": False}


@router.websocket("/stream/v2x")
async def stream_v2x(ws: WebSocket) -> None:
    await ws.accept()
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=64)
    _subscribers.append(queue)
    try:
        while True:
            try:
                await ws.send_json(await asyncio.wait_for(queue.get(), timeout=30.0))
            except TimeoutError:
                # Keep the socket alive without closing it. A timeout is an
                # idle interval, not a disconnected PC5 client.
                await ws.send_json({"type": "v2x.ping"})
    except WebSocketDisconnect:
        pass
    finally:
        if queue in _subscribers:
            _subscribers.remove(queue)
