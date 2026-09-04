from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from fastapi.testclient import TestClient

from services.gateway.app.client import WorldStateUnavailableError
from services.gateway.app.main import app, get_world_state_client


class StubWorldStateClient:
    async def health(self) -> Mapping[str, Any]:
        return {"status": "ok", "service": "world-state"}

    async def ingest_vehicle_state(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        return {"accepted": True, "actor_id": payload["actor_id"]}

    async def snapshot(self) -> Mapping[str, Any]:
        return {"version": 1, "actors": []}

    async def actors(self, query: Mapping[str, str]) -> Mapping[str, Any]:
        return {"actors": [], "query": dict(query)}


def vehicle_payload() -> dict[str, Any]:
    return {
        "schema_version": "v1",
        "actor_id": "b5d9e2bf-a7d5-4be0-bc23-31259db73cf2",
        "actor_type": "CAR",
        "ts": datetime.now(UTC).isoformat(),
        "position": {"lat": 12.9716, "lon": 77.5946},
        "position_uncertainty_m": 3.0,
        "speed_mps": 5.0,
        "heading_deg": 90.0,
        "source": "SIMULATION",
        "capabilities": [],
    }


def test_ingest_validates_and_forwards_canonical_vehicle_state() -> None:
    app.dependency_overrides[get_world_state_client] = lambda: StubWorldStateClient()
    try:
        with TestClient(app) as client:
            response = client.post("/v1/ingest/vehicle-state", json=vehicle_payload())
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 202
    assert response.json()["accepted"] is True


def test_world_actor_query_is_forwarded() -> None:
    app.dependency_overrides[get_world_state_client] = lambda: StubWorldStateClient()
    try:
        with TestClient(app) as client:
            response = client.get("/v1/world/actors?bbox=77,12,78,13&types=CAR")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["query"] == {"bbox": "77,12,78,13", "types": "CAR"}


def test_unavailable_world_state_is_reported_as_503() -> None:
    class UnavailableClient(StubWorldStateClient):
        async def health(self) -> Mapping[str, Any]:
            raise WorldStateUnavailableError()

    app.dependency_overrides[get_world_state_client] = lambda: UnavailableClient()
    try:
        with TestClient(app) as client:
            response = client.get("/health")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 503
