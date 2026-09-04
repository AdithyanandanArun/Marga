"""FastAPI transport for the canonical world-state read model."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from packages.schemas import VehicleState

from .store import BoundingBox, WorldStateStore


def _bbox(value: str | None) -> BoundingBox | None:
    if value is None:
        return None
    try:
        min_lon, min_lat, max_lon, max_lat = (float(part) for part in value.split(","))
    except ValueError as error:
        raise HTTPException(422, "bbox must be minLon,minLat,maxLon,maxLat") from error
    if (
        min_lon > max_lon
        or min_lat > max_lat
        or not (-180 <= min_lon <= max_lon <= 180 and -90 <= min_lat <= max_lat <= 90)
    ):
        raise HTTPException(422, "bbox must be ordered WGS84 bounds")
    return BoundingBox(min_lon=min_lon, min_lat=min_lat, max_lon=max_lon, max_lat=max_lat)


def _validate_vehicle(payload: dict) -> VehicleState:
    try:
        if hasattr(VehicleState, "model_validate"):
            return VehicleState.model_validate(payload)
        return VehicleState.parse_obj(payload)
    except ValidationError as error:
        raise HTTPException(422, detail=json.loads(error.json())) from error


def create_app(store: WorldStateStore | None = None) -> FastAPI:
    world = store or WorldStateStore()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.world_state = world
        yield

    app = FastAPI(title="Marga World State", version="0.1.0", lifespan=lifespan)

    @app.get("/health")
    async def health() -> dict[str, object]:
        counts = await world.consistency()
        return {"status": "ok", **counts}

    @app.post("/v1/ingest/vehicle-state", status_code=202)
    async def ingest_vehicle_state(payload: dict) -> dict[str, object]:
        accepted, record = await world.upsert_vehicle(_validate_vehicle(payload))
        return {"accepted": accepted, "actor_id": record.state.actor_id, "version": record.version}

    @app.get("/v1/world/snapshot")
    async def world_snapshot(
        bbox: str | None = None,
        types: Annotated[
            str | None, Query(description="comma-separated canonical actor types")
        ] = None,
        include_stale: bool = False,
    ) -> dict:
        actor_types = set(types.split(",")) if types else None
        return await world.snapshot(
            bbox=_bbox(bbox), actor_types=actor_types, include_stale=include_stale
        )

    @app.get("/v1/world/actors")
    async def world_actors(
        bbox: str, types: str | None = None, include_stale: bool = False
    ) -> dict[str, object]:
        actor_types = set(types.split(",")) if types else None
        parsed_bbox = _bbox(bbox)
        assert parsed_bbox is not None
        actors = await world.actors_in_bbox(
            parsed_bbox, actor_types=actor_types, include_stale=include_stale
        )
        return {"actors": actors}

    @app.websocket("/v1/stream/world")
    async def world_stream(websocket: WebSocket) -> None:
        # WebSocket query parameters are parsed independently to avoid an HTTP
        # exception escaping after the connection is accepted.
        raw_bbox = websocket.query_params.get("bbox")
        try:
            bbox = _bbox(raw_bbox)
        except HTTPException:
            await websocket.close(code=1008, reason="invalid bbox")
            return
        await websocket.accept()
        subscriber_id, queue = await world.subscribe(bbox)
        try:
            await websocket.send_json({"type": "world.snapshot", **await world.snapshot(bbox=bbox)})
            while True:
                await websocket.send_json(await queue.get())
        except WebSocketDisconnect:
            pass
        finally:
            await world.unsubscribe(subscriber_id)

    return app


app = create_app()
