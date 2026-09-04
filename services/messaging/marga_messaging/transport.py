"""V2X Transport abstraction — transport-neutral protocol with in-process and WebSocket adapters."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Callable, Protocol, runtime_checkable

import websockets
from marga_schemas.common import ConnectivityState
from marga_schemas.messaging import LinkState, V2XMessage
from websockets.asyncio.client import ClientConnection

logger = logging.getLogger(__name__)


@runtime_checkable
class V2XTransport(Protocol):
    """Transport-neutral V2X messaging protocol.

    Implementations must not depend on any specific broker (NATS, Kafka, etc.).
    """

    async def publish(self, topic: str, message: V2XMessage, qos: int = 0) -> bool:
        """Publish a V2X message to a topic. Returns True if accepted."""
        ...

    async def subscribe(self, topic_pattern: str, handler: Callable) -> str:
        """Subscribe to a topic pattern. Returns a subscription ID."""
        ...

    async def unsubscribe(self, subscription_id: str) -> None:
        """Remove a subscription by ID."""
        ...

    def get_link_state(self) -> LinkState:
        """Return current link state."""
        ...

    async def close(self) -> None:
        """Gracefully shut down the transport."""
        ...


def _is_expired(message: V2XMessage) -> bool:
    """Check whether a message's TTL has elapsed."""
    now = datetime.now(timezone.utc)
    ts = message.timestamp if message.timestamp.tzinfo else message.timestamp.replace(tzinfo=timezone.utc)
    elapsed = (now - ts).total_seconds()
    return elapsed > message.ttl_s


def _topic_matches(pattern: str, topic: str) -> bool:
    """Match a topic against a simple wildcard pattern.

    Supports '*' for single-level and '#' for multi-level wildcards, using
    '/' as the level separator (MQTT-style).
    """
    regex = "^" + re.escape(pattern).replace(r"\*", r"[^/]+").replace(r"\#", r".*") + "$"
    return re.match(regex, topic) is not None


class InProcessTransport:
    """In-memory pub/sub transport for unit tests.

    Uses async queues to deliver messages directly within a single process.
    All operations are synchronization-safe via asyncio locks.
    """

    def __init__(self, node_id: str = "test-node") -> None:
        self._node_id = node_id
        self._subscriptions: dict[str, tuple[str, Callable]] = {}  # sub_id -> (pattern, handler)
        self._lock = asyncio.Lock()
        self._closed = False
        self._publish_count = 0
        self._deliver_count = 0

    async def publish(self, topic: str, message: V2XMessage, qos: int = 0) -> bool:
        if self._closed:
            return False

        if _is_expired(message):
            logger.debug("Dropping expired message %s", message.message_id)
            return False

        self._publish_count += 1

        async with self._lock:
            subs = list(self._subscriptions.items())

        for sub_id, (pattern, handler) in subs:
            if _topic_matches(pattern, topic):
                try:
                    await handler(topic, message)
                    self._deliver_count += 1
                except Exception:
                    logger.exception("Handler error for subscription %s", sub_id)

        return True

    async def subscribe(self, topic_pattern: str, handler: Callable) -> str:
        sub_id = str(uuid.uuid4())
        async with self._lock:
            self._subscriptions[sub_id] = (topic_pattern, handler)
        return sub_id

    async def unsubscribe(self, subscription_id: str) -> None:
        async with self._lock:
            self._subscriptions.pop(subscription_id, None)

    def get_link_state(self) -> LinkState:
        return LinkState(
            node_id=self._node_id,
            connectivity=ConnectivityState.FULL,
            direct_peers=len(self._subscriptions),
            cloud_reachable=True,
            last_cloud_contact=datetime.now(timezone.utc),
            queue_depth={},
        )

    async def close(self) -> None:
        self._closed = True
        async with self._lock:
            self._subscriptions.clear()


class WebSocketTransport:
    """WebSocket-based V2X transport for software simulation.

    Connects to a configurable WebSocket endpoint and exchanges JSON-serialized
    V2XMessages. Supports topic-based pub/sub with server-side or client-side
    filtering.
    """

    def __init__(
        self,
        node_id: str,
        endpoint: str = "ws://localhost:8765",
        *,
        reconnect_interval: float = 5.0,
    ) -> None:
        self._node_id = node_id
        self._endpoint = endpoint
        self._reconnect_interval = reconnect_interval
        self._subscriptions: dict[str, tuple[str, Callable]] = {}
        self._ws: ClientConnection | None = None
        self._lock = asyncio.Lock()
        self._closed = False
        self._receive_task: asyncio.Task[None] | None = None
        self._cloud_reachable = False
        self._last_cloud_contact: datetime | None = None
        self._publish_count = 0

    async def _connect(self) -> ClientConnection:
        ws = await websockets.connect(self._endpoint)
        self._cloud_reachable = True
        self._last_cloud_contact = datetime.now(timezone.utc)
        return ws

    async def _ensure_connected(self) -> ClientConnection | None:
        if self._ws is None or self._ws.close_code is not None:
            try:
                self._ws = await self._connect()
                if self._receive_task is None or self._receive_task.done():
                    self._receive_task = asyncio.create_task(self._receive_loop())
            except Exception:
                logger.warning("WebSocket connection to %s failed", self._endpoint)
                self._cloud_reachable = False
                self._ws = None
                return None
        return self._ws

    async def _receive_loop(self) -> None:
        """Listen for incoming messages and dispatch to matching subscriptions."""
        while not self._closed:
            try:
                ws = self._ws
                if ws is None:
                    await asyncio.sleep(self._reconnect_interval)
                    await self._ensure_connected()
                    continue

                raw = await ws.recv()
                data = json.loads(raw)
                topic = data.get("topic", "")
                message = V2XMessage.model_validate(data.get("message", data))

                if _is_expired(message):
                    continue

                async with self._lock:
                    subs = list(self._subscriptions.items())

                for sub_id, (pattern, handler) in subs:
                    if _topic_matches(pattern, topic):
                        try:
                            await handler(topic, message)
                        except Exception:
                            logger.exception("Handler error for subscription %s", sub_id)

                self._last_cloud_contact = datetime.now(timezone.utc)

            except websockets.ConnectionClosed:
                logger.info("WebSocket connection closed, reconnecting...")
                self._ws = None
                self._cloud_reachable = False
                await asyncio.sleep(self._reconnect_interval)
            except Exception:
                logger.exception("Error in WebSocket receive loop")
                await asyncio.sleep(1)

    async def publish(self, topic: str, message: V2XMessage, qos: int = 0) -> bool:
        if self._closed:
            return False

        if _is_expired(message):
            return False

        ws = await self._ensure_connected()
        if ws is None:
            return False

        try:
            payload = {
                "topic": topic,
                "message": json.loads(message.model_dump_json()),
            }
            await ws.send(json.dumps(payload))
            self._publish_count += 1
            self._last_cloud_contact = datetime.now(timezone.utc)
            return True
        except Exception:
            logger.exception("Failed to publish message via WebSocket")
            self._cloud_reachable = False
            return False

    async def subscribe(self, topic_pattern: str, handler: Callable) -> str:
        sub_id = str(uuid.uuid4())
        async with self._lock:
            self._subscriptions[sub_id] = (topic_pattern, handler)

        # Start receive loop if not already running
        await self._ensure_connected()
        return sub_id

    async def unsubscribe(self, subscription_id: str) -> None:
        async with self._lock:
            self._subscriptions.pop(subscription_id, None)

    def get_link_state(self) -> LinkState:
        return LinkState(
            node_id=self._node_id,
            connectivity=(ConnectivityState.FULL if self._cloud_reachable else ConnectivityState.ISOLATED),
            direct_peers=0,
            cloud_reachable=self._cloud_reachable,
            last_cloud_contact=self._last_cloud_contact,
            queue_depth={},
        )

    async def close(self) -> None:
        self._closed = True
        if self._receive_task and not self._receive_task.done():
            self._receive_task.cancel()
            try:
                await self._receive_task
            except asyncio.CancelledError:
                pass
        if self._ws:
            await self._ws.close()
            self._ws = None
        async with self._lock:
            self._subscriptions.clear()
