"""FastAPI application for the Edge V2X service.

Exposes the endpoints required by the final implementation blueprint:

    WS  v2x.message       — stream of V2X messages between edge nodes
    WS  risk.created       — stream of risk events detected by edge nodes
    GET /nodes/:id/neighbours — peers within PC5 range of a node
    GET /nodes/:id/connectivity — connectivity state of a node

Additional management endpoints:
    GET  /health            — service health check
    GET  /nodes             — list all edge nodes
    POST /nodes             — create a new edge node
    POST /nodes/:id/state   — update an actor's state (triggers risk evaluation)
    POST /internet          — toggle internet availability
    GET  /risks             — list all active risks
    GET  /stats             — aggregate statistics
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from marga_schemas.messaging import V2XMessage
from packages.schemas.canonical import RiskEvent, VehicleState

from .manager import EdgeV2XManager

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Global manager instance
# ---------------------------------------------------------------------------
_manager: EdgeV2XManager | None = None


def get_manager() -> EdgeV2XManager:
    if _manager is None:
        raise HTTPException(status_code=503, detail="Edge V2X manager not initialised")
    return _manager


def set_manager(manager: EdgeV2XManager) -> None:
    global _manager
    _manager = manager


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class HealthResponse(BaseModel):
    status: str
    service: str
    version: str
    node_count: int
    internet_available: bool


class CreateNodeRequest(BaseModel):
    actor_id: str


class NodeInfo(BaseModel):
    actor_id: str
    has_state: bool
    peer_count: int
    neighbour_count: int
    internet_available: bool
    active_risk_type: str | None = None
    active_risk_score: float | None = None


class UpdateStateRequest(BaseModel):
    """Request to update an actor's state, triggering risk evaluation."""
    state: VehicleState


class RiskResponse(BaseModel):
    node_id: str
    risk_id: str
    risk_type: str
    severity: float
    confidence: float
    risk_score: float
    ttc_s: float | None
    affected_actor_ids: list[str]
    policy_version: str
    ts: str


class NeighbourInfo(BaseModel):
    node_id: str
    link_quality: float
    has_state: bool
    actor_type: str | None = None


class ConnectivityResponse(BaseModel):
    node_id: str
    connectivity: str
    direct_peers: int
    cloud_reachable: bool
    last_cloud_contact: str | None = None
    last_direct_contact: str | None = None
    internet_available: bool
    pc5_active: bool


class InternetRequest(BaseModel):
    available: bool


class StatsResponse(BaseModel):
    node_count: int
    internet_available: bool
    nodes: dict[str, Any]


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    global _manager
    if _manager is None:
        _manager = EdgeV2XManager()
    logger.info("Edge V2X service started (%d nodes)", len(_manager.node_ids))
    yield
    if _manager is not None:
        await _manager.shutdown()
    logger.info("Edge V2X service stopped")


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Marga Edge V2X Service",
    description="Simulated OBU/ECU edge nodes with PC5 direct communication, local risk evaluation, and offline-first safety delivery.",
    version="0.1.0",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# WebSocket connection manager for streaming
# ---------------------------------------------------------------------------


class WebSocketHub:
    """Manages WebSocket connections for streaming events."""

    def __init__(self) -> None:
        self._message_connections: list[WebSocket] = []
        self._risk_connections: list[WebSocket] = []
        self._lock = asyncio.Lock()

    async def add_message_connection(self, ws: WebSocket) -> None:
        await ws.accept()
        async with self._lock:
            self._message_connections.append(ws)

    async def remove_message_connection(self, ws: WebSocket) -> None:
        async with self._lock:
            if ws in self._message_connections:
                self._message_connections.remove(ws)

    async def add_risk_connection(self, ws: WebSocket) -> None:
        await ws.accept()
        async with self._lock:
            self._risk_connections.append(ws)

    async def remove_risk_connection(self, ws: WebSocket) -> None:
        async with self._lock:
            if ws in self._risk_connections:
                self._risk_connections.remove(ws)

    async def broadcast_message(self, message: V2XMessage) -> None:
        """Broadcast a V2X message to all connected WebSocket clients."""
        data = json.loads(message.model_dump_json())
        async with self._lock:
            connections = list(self._message_connections)
        for ws in connections:
            try:
                await ws.send_json({"type": "v2x.message", "data": data})
            except Exception:
                logger.debug("Failed to send to message WS client")

    async def broadcast_risk(self, risk: RiskEvent, node_id: str) -> None:
        """Broadcast a risk event to all connected WebSocket clients."""
        data = json.loads(risk.model_dump_json())
        async with self._lock:
            connections = list(self._risk_connections)
        for ws in connections:
            try:
                await ws.send_json({"type": "risk.created", "node_id": node_id, "data": data})
            except Exception:
                logger.debug("Failed to send to risk WS client")


_hub = WebSocketHub()


# Register hub as a listener on the manager when it's created.
def _ensure_listeners() -> None:
    mgr = get_manager()
    mgr.on_message(_hub.broadcast_message)
    mgr.on_risk(lambda risk, nid: asyncio.create_task(_hub.broadcast_risk(risk, nid)))


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    mgr = get_manager()
    return HealthResponse(
        status="healthy",
        service="edge-v2x",
        version="0.1.0",
        node_count=len(mgr.node_ids),
        internet_available=mgr.internet_available,
    )


@app.get("/nodes", response_model=list[NodeInfo])
async def list_nodes() -> list[NodeInfo]:
    mgr = get_manager()
    result: list[NodeInfo] = []
    for node in mgr.get_all_nodes():
        stats = node.stats
        result.append(NodeInfo(
            actor_id=node.actor_id,
            has_state=stats["has_state"],
            peer_count=stats["peer_count"],
            neighbour_count=stats["neighbour_count"],
            internet_available=stats["internet_available"],
            active_risk_type=stats.get("active_risk_type"),
            active_risk_score=stats.get("active_risk_score"),
        ))
    return result


@app.post("/nodes", response_model=NodeInfo, status_code=201)
async def create_node(request: CreateNodeRequest) -> NodeInfo:
    mgr = get_manager()
    if mgr.get_node(request.actor_id) is not None:
        raise HTTPException(status_code=409, detail=f"Node {request.actor_id} already exists")
    node = await mgr.create_node(request.actor_id)
    stats = node.stats
    return NodeInfo(
        actor_id=node.actor_id,
        has_state=stats["has_state"],
        peer_count=stats["peer_count"],
        neighbour_count=stats["neighbour_count"],
        internet_available=stats["internet_available"],
        active_risk_type=stats.get("active_risk_type"),
        active_risk_score=stats.get("active_risk_score"),
    )


@app.post("/nodes/{actor_id}/state", response_model=RiskResponse | None)
async def update_state(actor_id: str, request: UpdateStateRequest) -> RiskResponse | None:
    """Update an actor's state and trigger local risk evaluation.

    Returns the active risk if one was detected, else null.
    """
    _ensure_listeners()
    mgr = get_manager()
    state = request.state
    if state.actor_id != actor_id:
        raise HTTPException(
            status_code=422,
            detail=f"State actor_id {state.actor_id} does not match path actor_id {actor_id}",
        )
    risk = await mgr.update_actor_state(state)
    if risk is None:
        return None
    return RiskResponse(
        node_id=actor_id,
        risk_id=risk.risk_id,
        risk_type=risk.type.value,
        severity=risk.severity,
        confidence=risk.confidence,
        risk_score=risk.risk_score,
        ttc_s=risk.time_to_conflict_s,
        affected_actor_ids=risk.affected_actor_ids,
        policy_version=risk.policy_version,
        ts=risk.ts.isoformat(),
    )


@app.get("/nodes/{actor_id}/neighbours", response_model=list[NeighbourInfo])
async def get_neighbours(actor_id: str) -> list[NeighbourInfo]:
    """Get peers within PC5 communication range of a node."""
    mgr = get_manager()
    neighbours = mgr.get_neighbours(actor_id)
    if neighbours is None:
        raise HTTPException(status_code=404, detail=f"Node {actor_id} not found")
    return [
        NeighbourInfo(
            node_id=n["node_id"],
            link_quality=n["link_quality"],
            has_state=n["has_state"],
            actor_type=n.get("actor_type"),
        )
        for n in neighbours
    ]


@app.get("/nodes/{actor_id}/connectivity", response_model=ConnectivityResponse)
async def get_connectivity(actor_id: str) -> ConnectivityResponse:
    """Get the connectivity state of a node.

    Shows whether the node has internet (cloud) and PC5 (direct) connectivity.
    When internet is off, cloud_reachable is False but pc5_active remains True.
    """
    mgr = get_manager()
    conn = mgr.get_connectivity(actor_id)
    if conn is None:
        raise HTTPException(status_code=404, detail=f"Node {actor_id} not found")
    return ConnectivityResponse(**conn)


@app.post("/internet", response_model=HealthResponse)
async def set_internet(request: InternetRequest) -> HealthResponse:
    """Toggle internet availability for all nodes.

    When internet is off:
    - Cloud delivery is removed
    - Local PC5 safety delivery continues
    - All nodes transition to DIRECT_ONLY connectivity
    """
    mgr = get_manager()
    mgr.set_internet(request.available)
    return HealthResponse(
        status="healthy",
        service="edge-v2x",
        version="0.1.0",
        node_count=len(mgr.node_ids),
        internet_available=mgr.internet_available,
    )


@app.get("/risks", response_model=list[RiskResponse])
async def get_all_risks() -> list[RiskResponse]:
    """List all active risks across all edge nodes."""
    mgr = get_manager()
    risks = mgr.get_all_risks()
    return [
        RiskResponse(
            node_id=r["node_id"],
            risk_id=r["risk_id"],
            risk_type=r["risk_type"],
            severity=r["severity"],
            confidence=r["confidence"],
            risk_score=r["risk_score"],
            ttc_s=r.get("ttc_s"),
            affected_actor_ids=r["affected_actor_ids"],
            policy_version=r["policy_version"],
            ts=r["ts"],
        )
        for r in risks
    ]


@app.get("/stats", response_model=StatsResponse)
async def get_stats() -> StatsResponse:
    """Return aggregate statistics for all nodes."""
    mgr = get_manager()
    stats = mgr.get_all_stats()
    return StatsResponse(**stats)


# ---------------------------------------------------------------------------
# WebSocket endpoints
# ---------------------------------------------------------------------------


@app.websocket("/v2x.message")
async def v2x_message_ws(websocket: WebSocket) -> None:
    """WebSocket stream of V2X messages between edge nodes.

    Clients receive every V2X message sent by any edge node, including
    actor state broadcasts and safety-critical risk messages.

    When internet is off, only PC5-delivered messages are streamed
    (cloud-only messages are not generated).
    """
    await _hub.add_message_connection(websocket)
    try:
        while True:
            # Keep connection alive; messages are pushed via broadcast
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        await _hub.remove_message_connection(websocket)


@app.websocket("/risk.created")
async def risk_created_ws(websocket: WebSocket) -> None:
    """WebSocket stream of risk events detected by edge nodes.

    Each message contains the full RiskEvent with evidence, confidence,
    and policy version.  Only the single prioritised active risk per
    node is streamed (not every pairwise detection).
    """
    await _hub.add_risk_connection(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        await _hub.remove_risk_connection(websocket)
