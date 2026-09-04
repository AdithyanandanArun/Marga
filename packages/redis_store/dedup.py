"""Redis-backed alert deduplication — sliding window prevents duplicate alerts."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("marga.redis.dedup")


class AlertDeduplicator:
    def __init__(self, redis_url: str = "redis://localhost:6379/0", window_s: int = 60) -> None:
        self._url = redis_url
        self._window = window_s
        self._redis: Any = None
        self._connected = False

    async def connect(self) -> None:
        try:
            import redis.asyncio as aioredis

            self._redis = aioredis.from_url(self._url, decode_responses=True)
            await self._redis.ping()
            self._connected = True
        except Exception as exc:
            logger.warning("Redis unavailable for dedup: %s", exc)
            self._connected = False

    async def is_duplicate(self, alert_type: str, actor_ids: list[str]) -> bool:
        if not self._connected or self._redis is None:
            return False
        key = f"alert_dedup:{alert_type}:{','.join(sorted(actor_ids))}"
        try:
            if await self._redis.exists(key):
                return True
            await self._redis.setex(key, self._window, "1")
            return False
        except Exception:
            return False

    async def close(self) -> None:
        if self._redis is not None:
            try:
                await self._redis.aclose()
            except Exception:
                pass
            self._connected = False
