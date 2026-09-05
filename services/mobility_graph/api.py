"""REST and WebSocket API for the canonical live mobility graph."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect

from packages.schemas.mobility_graph import GraphEdgeDefinition

from .service import mobility_graph

router = APIRouter(prefix="/graph", tags=["mobility-graph"])


@router.post("/edges", status_code=201)
async def register_edge(definition: GraphEdgeDefinition) -> dict[str, Any]:
    return mobility_graph.register_edge(definition).model_dump(mode="json")


@router.get("/edges/{edge_id}")
async def get_edge(edge_id: str) -> dict[str, Any]:
    state = mobility_graph.get_edge(edge_id)
    if state is None:
        raise HTTPException(status_code=404, detail=f"unknown graph edge {edge_id!r}")
    return state.model_dump(mode="json")


@router.get("/intersections/{intersection_id}")
async def get_intersection(intersection_id: str) -> dict[str, Any]:
    state = mobility_graph.get_intersection(intersection_id)
    if state is None:
        raise HTTPException(status_code=404, detail=f"unknown graph intersection {intersection_id!r}")
    return state.model_dump(mode="json")


@router.websocket("/stream")
async def stream_graph(ws: WebSocket) -> None:
    await ws.accept()
    queue = mobility_graph.subscribe()
    try:
        while True:
            try:
                await ws.send_json(await asyncio.wait_for(queue.get(), timeout=30.0))
            except TimeoutError:
                await ws.send_json({"event_type": "graph.ping", "data": {}})
    except WebSocketDisconnect:
        pass
    finally:
        mobility_graph.unsubscribe(queue)
