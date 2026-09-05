"""Vehicle route store — remembers current path and last RouteChange per vehicle."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from .pathfinder import PathStep


@dataclass
class RouteRecord:
    vehicle_id: str
    origin_node: str
    destination_node: str
    current_path: list[PathStep]
    old_path: list[PathStep]
    old_eta_s: float
    new_eta_s: float
    reason: str
    changed_at: str
    old_geometry: list[dict]
    new_geometry: list[dict]


class RouteStore:
    """Thread-safe-ish in-memory store for vehicle routes and last change events."""

    def __init__(self) -> None:
        self._records: dict[str, RouteRecord] = {}
        self._subscribers: list = []   # asyncio.Queue instances

    def put(self, record: RouteRecord) -> None:
        self._records[record.vehicle_id] = record

    def get(self, vehicle_id: str) -> Optional[RouteRecord]:
        return self._records.get(vehicle_id)

    def all_vehicle_ids(self) -> list[str]:
        return list(self._records.keys())

    def get_path(self, vehicle_id: str) -> list[PathStep]:
        rec = self._records.get(vehicle_id)
        return rec.current_path if rec else []

    def to_route_change(self, vehicle_id: str) -> Optional[dict]:
        rec = self._records.get(vehicle_id)
        if rec is None:
            return None
        return {
            "vehicle_id": rec.vehicle_id,
            "old_route": rec.old_geometry,
            "new_route": rec.new_geometry,
            "old_eta_s": rec.old_eta_s,
            "new_eta_s": rec.new_eta_s,
            "reason": rec.reason,
            "changed_at": rec.changed_at,
        }

    def subscribe(self, queue) -> None:  # type: ignore[type-arg]
        self._subscribers.append(queue)

    def unsubscribe(self, queue) -> None:  # type: ignore[type-arg]
        self._subscribers = [q for q in self._subscribers if q is not queue]

    async def broadcast(self, change: dict) -> None:
        import json
        payload = json.dumps(change)
        dead = []
        for q in self._subscribers:
            try:
                q.put_nowait(payload)
            except Exception:
                dead.append(q)
        for q in dead:
            self.unsubscribe(q)
