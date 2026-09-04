"""Redis-backed actor TTL management — stale actors expire automatically."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("marga.redis.actor_ttl")

_manager: ActorTTLManager | None = None


class ActorTTLManager:
    def __init__(self, redis_url: str = "redis://localhost:6379/0", default_ttl_s: int = 30) -> None:
        self._url = redis_url
        self._ttl = default_ttl_s
        self._redis: Any = None
        self._connected = False

    @property
    def connected(self) -> bool:
        return self._connected

    async def connect(self) -> None:
        try:
            import redis.asyncio as aioredis

            self._redis = aioredis.from_url(
                self._url,
                decode_responses=True,
                socket_connect_timeout=0.25,
                socket_timeout=0.25,
            )
            await self._redis.ping()
            self._connected = True
            logger.info("ActorTTLManager connected to %s", self._url)
        except Exception as exc:
            logger.warning("Redis unavailable (%s) — actor TTL disabled", exc)
            self._connected = False

    async def touch(self, actor_id: str, state_json: str) -> None:
        if not self._connected or self._redis is None:
            return
        try:
            await self._redis.setex(f"actor:{actor_id}", self._ttl, state_json)
        except Exception as exc:
            logger.debug("Redis touch failed for %s: %s", actor_id, exc)

    async def get(self, actor_id: str) -> str | None:
        if not self._connected or self._redis is None:
            return None
        try:
            return await self._redis.get(f"actor:{actor_id}")
        except Exception:
            return None

    async def get_all_active_ids(self) -> list[str]:
        if not self._connected or self._redis is None:
            return []
        try:
            keys: list[str] = []
            async for key in self._redis.scan_iter("actor:*"):
                keys.append(key.removeprefix("actor:"))
            return keys
        except Exception:
            return []

    async def get_expired_ids(self, known_ids: set[str]) -> set[str]:
        """Return IDs from known_ids that are no longer in Redis (expired)."""
        if not self._connected:
            return set()
        active = set(await self.get_all_active_ids())
        return known_ids - active

    async def close(self) -> None:
        if self._redis is not None:
            try:
                await self._redis.aclose()
            except Exception:
                pass
            self._connected = False


def get_ttl_manager() -> ActorTTLManager | None:
    return _manager


def set_ttl_manager(mgr: ActorTTLManager) -> None:
    global _manager
    _manager = mgr
