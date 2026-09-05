"""Cooperative routing FastAPI service — Adithyan2 scope.

Endpoints:
  POST /v1/routes/recalculate          — recalculate route for one vehicle
  GET  /v1/routes/{vehicle_id}         — current route for a vehicle
  WS   /v1/stream/routes               — route.changed stream
  POST /v1/graph/edge                  — ingest live edge metrics from Adithyan1
  GET  /v1/graph/nodes                 — list all graph nodes (debug)
"""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from .distributor import CooperativeDistributor
from .graph import MobilityGraph
from .mock_graph import build_mock_graph
from .pathfinder import find_path, path_eta_s, path_to_geometry
from .store import RouteRecord, RouteStore

log = logging.getLogger(__name__)

_graph: MobilityGraph
_store: RouteStore
_distributor: CooperativeDistributor

# Default endpoints: vehicles start at rail_crossing and go to roundabout
DEFAULT_ORIGIN = "rail_crossing"
DEFAULT_DESTINATION = "roundabout"


@asynccontextmanager
async def lifespan(app: FastAPI):  # type: ignore[type-arg]
    global _graph, _store, _distributor
    _graph = build_mock_graph()
    _store = RouteStore()
    _distributor = CooperativeDistributor(_graph)
    log.info("Routing graph loaded: %d nodes, %d edges", _graph.node_count(), _graph.edge_count())
    yield


app = FastAPI(
    title="Marga Cooperative Routing",
    description="A* routing with composite edge costs and cooperative load balancing",
    version="1.0.0",
    lifespan=lifespan,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Request / response models ─────────────────────────────────────────────────

class RecalculateRequest(BaseModel):
    vehicle_id: str
    origin_node: Optional[str] = None       # defaults to DEFAULT_ORIGIN
    destination_node: Optional[str] = None  # defaults to DEFAULT_DESTINATION


class RecalculateResponse(BaseModel):
    vehicle_id: str
    old_route: list[dict]
    new_route: list[dict]
    old_eta_s: float
    new_eta_s: float
    reason: str
    changed_at: str
    rerouted: bool


class EdgeMetricsIngest(BaseModel):
    edge_id: str
    avg_speed_mps: float = 8.0
    vehicle_count: int = 0
    queue_length: int = 0
    capacity_ratio: float = 0.0
    hazard_penalty: float = 0.0
    gps_confidence: float = 1.0
    downstream_congestion: float = 0.0
    two_wheeler_ratio: float = 0.0
    flow_rate_vph: float = 0.0
    occupancy: float = 0.0
    closure: bool = False


# ── REST endpoints ────────────────────────────────────────────────────────────

@app.post("/v1/routes/recalculate", response_model=RecalculateResponse)
async def recalculate(body: RecalculateRequest) -> RecalculateResponse:
    origin = body.origin_node or DEFAULT_ORIGIN
    dest = body.destination_node or DEFAULT_DESTINATION

    if _graph.node(origin) is None:
        raise HTTPException(404, f"Origin node {origin!r} not in graph")
    if _graph.node(dest) is None:
        raise HTTPException(404, f"Destination node {dest!r} not in graph")

    old_path = _store.get_path(body.vehicle_id)
    if not old_path:
        old_path, _ = find_path(_graph, origin, dest)

    assignments = _distributor.plan(
        vehicle_routes={body.vehicle_id: (origin, dest)},
        current_paths={body.vehicle_id: old_path},
    )
    assignment = assignments[0]

    old_geo = path_to_geometry(_graph, assignment.old_path)
    new_geo = path_to_geometry(_graph, assignment.new_path)
    now = _now()

    record = RouteRecord(
        vehicle_id=body.vehicle_id,
        origin_node=origin,
        destination_node=dest,
        current_path=assignment.new_path,
        old_path=assignment.old_path,
        old_eta_s=assignment.old_eta_s,
        new_eta_s=assignment.new_eta_s,
        reason=assignment.reason or "no_change",
        changed_at=now,
        old_geometry=old_geo,
        new_geometry=new_geo,
    )
    _store.put(record)

    change = _store.to_route_change(body.vehicle_id)
    if change and assignment.triggered:
        await _store.broadcast(change)

    return RecalculateResponse(
        vehicle_id=body.vehicle_id,
        old_route=old_geo,
        new_route=new_geo,
        old_eta_s=assignment.old_eta_s,
        new_eta_s=assignment.new_eta_s,
        reason=assignment.reason or "no_change",
        changed_at=now,
        rerouted=assignment.triggered,
    )


@app.get("/v1/routes/{vehicle_id}")
def get_vehicle_route(vehicle_id: str) -> dict:
    change = _store.to_route_change(vehicle_id)
    if change is None:
        raise HTTPException(404, f"No route found for vehicle {vehicle_id!r}")
    return change


@app.post("/v1/graph/edge")
def ingest_edge_metrics(body: EdgeMetricsIngest) -> dict:
    """Called by Adithyan1's mobility graph service to push live edge metrics."""
    _graph.ingest_metrics_dict(body.edge_id, body.model_dump())
    return {"ok": True, "edge_id": body.edge_id}


@app.post("/v1/graph/edges/batch")
def ingest_edge_metrics_batch(edges: list[EdgeMetricsIngest]) -> dict:
    for e in edges:
        _graph.ingest_metrics_dict(e.edge_id, e.model_dump())
    return {"ok": True, "updated": len(edges)}


@app.get("/v1/graph/nodes")
def list_nodes() -> dict:
    return {
        "nodes": [
            {"node_id": n.node_id, "lat": n.lat, "lon": n.lon, "label": n.label}
            for n in _graph.all_nodes()
        ],
        "edge_count": _graph.edge_count(),
    }


@app.get("/v1/graph/edges")
def list_edges() -> dict:
    return {
        "edges": [
            {
                "edge_id": e.edge_id,
                "src": e.src,
                "dst": e.dst,
                "length_m": e.length_m,
                "avg_speed_mps": e.metrics.avg_speed_mps,
                "capacity_ratio": e.metrics.capacity_ratio,
                "hazard_penalty": e.metrics.hazard_penalty,
                "closure": e.metrics.closure,
            }
            for e in _graph.all_edges()
        ]
    }


# ── WebSocket stream ──────────────────────────────────────────────────────────

@app.websocket("/v1/stream/routes")
async def stream_routes(ws: WebSocket) -> None:
    await ws.accept()
    queue: asyncio.Queue[str] = asyncio.Queue()
    _store.subscribe(queue)
    try:
        await ws.send_json({"event_type": "routing.connected", "ts": _now()})
        while True:
            payload = await asyncio.wait_for(queue.get(), timeout=30.0)
            change = json.loads(payload)
            await ws.send_json({"event_type": "route.changed", "data": change})
    except asyncio.TimeoutError:
        await ws.send_json({"event_type": "routing.ping", "ts": _now()})
    except WebSocketDisconnect:
        pass
    finally:
        _store.unsubscribe(queue)
