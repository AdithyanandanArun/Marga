"""NATS JetStream event bus for canonical event families."""

from __future__ import annotations

import json
import logging
from typing import Any, Callable, Coroutine

logger = logging.getLogger("marga.event_bus")

_STREAMS: list[tuple[str, list[str]]] = [
    ("ACTORS", ["actor.state.>"]),
    ("RISKS", ["risk.>", "alert.>"]),
    ("HAZARDS", ["hazard.>"]),
    ("INFRA", ["infrastructure.>", "connectivity.>", "system.>"]),
    ("TRUST", ["trust.>"]),
]


class EventBus:
    """Async NATS JetStream event bus.

    All methods are safe to call even when NATS is unavailable — they log
    warnings and return gracefully so callers never need try/except.
    """

    def __init__(self, nats_url: str = "nats://localhost:4222") -> None:
        self._url = nats_url
        self._nc: Any = None
        self._js: Any = None
        self._connected = False

    @property
    def connected(self) -> bool:
        return self._connected

    async def connect(self) -> None:
        try:
            import nats  # type: ignore[import-untyped]

            self._nc = await nats.connect(self._url)
            self._js = self._nc.jetstream()
            for stream_name, subjects in _STREAMS:
                try:
                    await self._js.add_stream(name=stream_name, subjects=subjects)
                except Exception:
                    pass
            self._connected = True
            logger.info("EventBus connected to %s", self._url)
        except Exception as exc:
            logger.warning("EventBus unavailable (%s) — running without NATS", exc)
            self._connected = False

    async def publish(self, subject: str, data: dict[str, Any]) -> None:
        if not self._connected or self._js is None:
            return
        try:
            payload = json.dumps(data, default=str).encode()
            await self._js.publish(subject, payload)
        except Exception as exc:
            logger.warning("EventBus publish failed on %s: %s", subject, exc)

    async def subscribe(
        self,
        subject: str,
        handler: Callable[..., Coroutine[Any, Any, None]],
        durable: str | None = None,
    ) -> Any:
        if not self._connected or self._js is None:
            return None
        try:
            sub = await self._js.subscribe(subject, durable=durable, cb=handler)
            logger.info("Subscribed to %s", subject)
            return sub
        except Exception as exc:
            logger.warning("EventBus subscribe failed on %s: %s", subject, exc)
            return None

    async def close(self) -> None:
        if self._nc is not None:
            try:
                await self._nc.close()
            except Exception:
                pass
            self._connected = False


# Module-level singleton — None until connect() is called from app lifespan.
_bus: EventBus | None = None


def get_event_bus() -> EventBus | None:
    return _bus


def set_event_bus(bus: EventBus) -> None:
    global _bus
    _bus = bus
