from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from packages.schemas import VehicleState
from services.world_state.app import create_app
from services.world_state.store import BoundingBox, WorldStateStore


def vehicle_payload(
    *, actor_id: str = "vehicle-1", ts: datetime | None = None, lon: float = 77.5946
) -> dict:
    return {
        "schema_version": "v0",
        "actor_id": actor_id,
        "actor_type": "CAR",
        "ts": (ts or datetime.now(UTC)).isoformat(),
        "position": {"lat": 12.9716, "lon": lon},
        "position_uncertainty_m": 2.0,
        "speed_mps": 7.5,
        "heading_deg": 90.0,
        "source": "SIMULATION",
        "capabilities": [],
    }


def vehicle(**kwargs: object) -> VehicleState:
    return VehicleState.model_validate(vehicle_payload(**kwargs))


def test_newer_state_replaces_older_and_out_of_order_update_is_rejected() -> None:
    async def exercise() -> None:
        store = WorldStateStore()
        now = datetime.now(UTC)
        accepted, first = await store.upsert_vehicle(vehicle(ts=now, lon=77.59))
        rejected, current = await store.upsert_vehicle(
            vehicle(ts=now - timedelta(seconds=1), lon=77.60)
        )
        assert accepted is True
        assert rejected is False
        assert first.version == current.version == 1
        snapshot = await store.snapshot(include_stale=True)
        assert snapshot["actors"][0]["state"]["position"]["lon"] == 77.59

    asyncio.run(exercise())


def test_snapshot_and_subscriber_are_viewport_scoped() -> None:
    async def exercise() -> None:
        store = WorldStateStore()
        west_bengaluru = BoundingBox(77.50, 12.90, 77.60, 13.00)
        _, messages = await store.subscribe(west_bengaluru)
        accepted, _ = await store.upsert_vehicle(vehicle(lon=77.5946))
        await store.upsert_vehicle(vehicle(actor_id="outside", lon=77.70))
        assert accepted is True
        update = messages.get_nowait()
        assert update["actor_id"] == "vehicle-1"
        assert update["envelope"]["event_type"] == "actor.state.updated"
        assert messages.empty()
        snapshot = await store.snapshot(bbox=west_bengaluru)
        assert [actor["state"]["actor_id"] for actor in snapshot["actors"]] == ["vehicle-1"]

    asyncio.run(exercise())


def test_ingest_snapshot_and_websocket_delta() -> None:
    app = create_app(WorldStateStore())
    with TestClient(app) as client:
        with client.websocket_connect("/v1/stream/world?bbox=77.50,12.90,77.60,13.00") as stream:
            initial = stream.receive_json()
            assert initial["type"] == "world.snapshot"
            response = client.post("/v1/ingest/vehicle-state", json=vehicle_payload())
            assert response.status_code == 202
            delta = stream.receive_json()
            assert delta["type"] == "actor.upsert"
            assert delta["actor_id"] == "vehicle-1"
        snapshot = client.get("/v1/world/snapshot")
        assert snapshot.status_code == 200
        assert snapshot.json()["actors"][0]["freshness"] == "ACTIVE"
