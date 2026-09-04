"""Marga's public REST and WebSocket gateway."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, cast

from fastapi import Depends, FastAPI, Request, WebSocket, WebSocketDisconnect, status
from fastapi.responses import JSONResponse

from packages.schemas import VehicleState

from .client import WorldStateClient, WorldStateUnavailableError
from .config import Settings, get_settings


def get_world_state_client(request: Request) -> WorldStateClient:
    return cast(WorldStateClient, request.app.state.world_state_client)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    app.state.settings = settings
    app.state.world_state_client = WorldStateClient(settings.world_state_url)
    try:
        yield
    finally:
        await app.state.world_state_client.close()


app = FastAPI(title="Marga Gateway", version="0.1.0", lifespan=lifespan)


@app.exception_handler(WorldStateUnavailableError)
async def world_state_unavailable(_: Request, __: WorldStateUnavailableError) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={"detail": "authoritative world-state service is unavailable"},
    )


@app.get("/health")
async def health(
    client: WorldStateClient = Depends(get_world_state_client),  # noqa: B008
) -> dict[str, Any]:
    """Report gateway health and whether its required state dependency responds."""
    world_state = await client.health()
    return {"status": "ok", "service": "gateway", "world_state": world_state}


@app.post("/v1/ingest/vehicle-state", status_code=status.HTTP_202_ACCEPTED)
async def ingest_vehicle_state(
    vehicle_state: VehicleState,
    client: WorldStateClient = Depends(get_world_state_client),  # noqa: B008
) -> dict[str, Any]:
    """Validate and forward canonical state; no SUMO or UI-shaped payloads enter here."""
    return dict(await client.ingest_vehicle_state(vehicle_state.model_dump(mode="json")))


@app.get("/v1/world/snapshot")
async def world_snapshot(
    client: WorldStateClient = Depends(get_world_state_client),  # noqa: B008
) -> dict[str, Any]:
    return dict(await client.snapshot())


@app.get("/v1/world/actors")
async def world_actors(
    request: Request,
    client: WorldStateClient = Depends(get_world_state_client),  # noqa: B008
) -> dict[str, Any]:
    # Preserve only explicitly supplied query parameters; world-state owns their semantics.
    return dict(await client.actors(dict(request.query_params)))


@app.websocket("/v1/stream/world")
async def stream_world(websocket: WebSocket) -> None:
    """Bridge the authoritative world stream without retaining a second state copy."""
    await websocket.accept()
    settings: Settings = websocket.app.state.settings
    query = websocket.url.query
    target = f"{settings.world_state_ws_url}/v1/stream/world"
    if query:
        target = f"{target}?{query}"

    from websockets.asyncio.client import connect as ws_connect

    try:
        async with ws_connect(target) as upstream:
            while True:
                message = await upstream.recv()
                if isinstance(message, bytes):
                    await websocket.send_bytes(message)
                else:
                    await websocket.send_text(message)
    except WebSocketDisconnect:
        return
    except Exception:
        # WebSocket responses cannot change status after accept; give clients an explicit error.
        await websocket.send_json(
            {"type": "stream.error", "detail": "world-state stream unavailable"}
        )
        await websocket.close(code=status.WS_1011_INTERNAL_ERROR)
