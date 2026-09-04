"""Concurrency-safe current world state and viewport-scoped change streams.

This is deliberately an in-memory read model.  It accepts canonical entities,
keeps only the newest observation per actor, and can later be replaced by a
durable/indexed implementation without changing the HTTP or stream contract.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast
from uuid import uuid4

from packages.schemas import EventEnvelope, EventType, VehicleState


@dataclass(frozen=True)
class BoundingBox:
    """WGS84 viewport bounds; dateline-crossing boxes are intentionally rejected."""

    min_lon: float
    min_lat: float
    max_lon: float
    max_lat: float

    def contains(self, lat: float, lon: float) -> bool:
        return self.min_lat <= lat <= self.max_lat and self.min_lon <= lon <= self.max_lon


@dataclass
class VersionedVehicle:
    state: VehicleState
    version: int
    received_at: datetime


@dataclass
class Subscriber:
    queue: asyncio.Queue[dict[str, Any]]
    bbox: BoundingBox | None


class WorldStateStore:
    """The authoritative latest-state map for vehicle actors.

    Timestamp ordering, state replacement, and subscriber selection happen
    under one async lock, so a reader never observes an actor in both its old
    and new locations.  The current simple index is a dictionary scan; its
    narrow query interface is the seam for R-tree/H3/PostGIS at larger scale.
    """

    def __init__(
        self,
        *,
        active_after_s: float = 5.0,
        degraded_after_s: float = 15.0,
        stale_after_s: float = 30.0,
        subscriber_queue_size: int = 256,
    ) -> None:
        if not 0 < active_after_s <= degraded_after_s <= stale_after_s:
            raise ValueError("freshness thresholds must be positive and ordered")
        self.active_after_s = active_after_s
        self.degraded_after_s = degraded_after_s
        self.stale_after_s = stale_after_s
        self.subscriber_queue_size = subscriber_queue_size
        self._actors: dict[str, VersionedVehicle] = {}
        self._subscribers: dict[str, Subscriber] = {}
        self._lock = asyncio.Lock()

    @staticmethod
    def _now() -> datetime:
        return datetime.now(UTC)

    @staticmethod
    def _json(value: Any) -> Any:
        """Serialize Pydantic v2 canonical schemas without leaking Python types."""
        if hasattr(value, "model_dump"):
            return value.model_dump(mode="json")
        return value

    def _freshness(self, item: VersionedVehicle, now: datetime) -> str:
        age_s = max(0.0, (now - item.state.ts).total_seconds())
        if age_s <= self.active_after_s:
            return "ACTIVE"
        if age_s <= self.degraded_after_s:
            return "DEGRADED"
        return "STALE"

    def _visible(
        self, item: VersionedVehicle, bbox: BoundingBox | None, now: datetime, include_stale: bool
    ) -> bool:
        if not include_stale and self._freshness(item, now) == "STALE":
            return False
        position = item.state.position
        return bbox is None or bbox.contains(position.lat, position.lon)

    def _event(self, kind: str, item: VersionedVehicle, now: datetime) -> dict[str, Any]:
        envelope = EventEnvelope[VehicleState](
            event_type=EventType.ACTOR_STATE_UPDATED,
            produced_at=now,
            source_service="world-state",
            actor_id=item.state.actor_id,
            payload=item.state,
        )
        return {
            "event_id": str(envelope.event_id),
            "type": kind,
            "entity_type": "actor",
            "actor_id": item.state.actor_id,
            "version": item.version,
            "freshness": self._freshness(item, now),
            "state": self._json(item.state),
            "envelope": self._json(envelope),
        }

    def _offer(self, subscriber: Subscriber, message: dict[str, Any]) -> None:
        # A slow UI client must not block authoritative ingest.  Dropping an old
        # delta is safe because clients can reconnect and obtain a snapshot.
        try:
            subscriber.queue.put_nowait(message)
        except asyncio.QueueFull:
            with suppress(asyncio.QueueEmpty):
                subscriber.queue.get_nowait()
            with suppress(asyncio.QueueFull):
                subscriber.queue.put_nowait(message)

    async def upsert_vehicle(self, state: VehicleState) -> tuple[bool, VersionedVehicle]:
        """Store a vehicle if it is newer than the existing observation.

        Equal timestamps are treated as duplicates.  Returns ``(accepted,
        record)``; callers should report a rejected update as a no-op rather
        than an ingest failure so adapters can safely retry messages.
        """
        now = self._now()
        async with self._lock:
            previous = self._actors.get(state.actor_id)
            if previous is not None and state.ts <= previous.state.ts:
                return False, previous
            record = VersionedVehicle(
                state=state,
                version=1 if previous is None else previous.version + 1,
                received_at=now,
            )
            self._actors[state.actor_id] = record
            message = self._event("actor.upsert", record, now)
            for subscriber in self._subscribers.values():
                if self._visible(record, subscriber.bbox, now, include_stale=True):
                    self._offer(subscriber, message)
            return True, record

    async def snapshot(
        self,
        *,
        bbox: BoundingBox | None = None,
        actor_types: set[str] | None = None,
        include_stale: bool = False,
    ) -> dict[str, Any]:
        now = self._now()
        async with self._lock:
            actors = []
            for item in self._actors.values():
                if (
                    actor_types
                    and str(item.state.actor_type) not in actor_types
                    and getattr(item.state.actor_type, "value", None) not in actor_types
                ):
                    continue
                if self._visible(item, bbox, now, include_stale):
                    actors.append(
                        {
                            "version": item.version,
                            "freshness": self._freshness(item, now),
                            "state": self._json(item.state),
                        }
                    )
            return {
                "generated_at": now.isoformat(),
                "actors": actors,
                "hazards": [],
                "road_events": [],
                "signals": [],
            }

    async def actors_in_bbox(
        self, bbox: BoundingBox, *, actor_types: set[str] | None = None, include_stale: bool = False
    ) -> list[dict[str, Any]]:
        snapshot = await self.snapshot(
            bbox=bbox, actor_types=actor_types, include_stale=include_stale
        )
        return cast(list[dict[str, Any]], snapshot["actors"])

    async def subscribe(
        self, bbox: BoundingBox | None = None
    ) -> tuple[str, asyncio.Queue[dict[str, Any]]]:
        subscriber_id = str(uuid4())
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=self.subscriber_queue_size)
        async with self._lock:
            self._subscribers[subscriber_id] = Subscriber(queue=queue, bbox=bbox)
        return subscriber_id, queue

    async def unsubscribe(self, subscriber_id: str) -> None:
        async with self._lock:
            self._subscribers.pop(subscriber_id, None)

    async def consistency(self) -> dict[str, int]:
        """Development/test hook; a future spatial index must match this count."""
        async with self._lock:
            return {
                "latest_actor_count": len(self._actors),
                "spatial_actor_count": len(self._actors),
            }
