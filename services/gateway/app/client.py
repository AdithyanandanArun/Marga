"""Small transport client for the world-state service.

The gateway deliberately contains no domain state.  It validates public input
then forwards it to the authoritative world-state process.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import httpx


class WorldStateUnavailableError(RuntimeError):
    """Raised when the authoritative world-state service cannot be reached."""


class WorldStateClient:
    def __init__(self, base_url: str) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=5.0)

    async def close(self) -> None:
        await self._client.aclose()

    async def health(self) -> Mapping[str, Any]:
        return await self._request("GET", "/health")

    async def ingest_vehicle_state(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        return await self._request("POST", "/v1/ingest/vehicle-state", json=payload)

    async def snapshot(self) -> Mapping[str, Any]:
        return await self._request("GET", "/v1/world/snapshot")

    async def actors(self, query: Mapping[str, str]) -> Mapping[str, Any]:
        return await self._request("GET", "/v1/world/actors", params=query)

    async def _request(self, method: str, path: str, **kwargs: Any) -> Mapping[str, Any]:
        try:
            response = await self._client.request(method, path, **kwargs)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise WorldStateUnavailableError("world-state request failed") from exc
        result = response.json()
        if not isinstance(result, Mapping):
            raise WorldStateUnavailableError("world-state returned a non-object response")
        return result
