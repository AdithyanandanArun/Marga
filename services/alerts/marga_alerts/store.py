"""In-memory alert storage with spatial and attribute queries.

Provides a bounded history buffer for resolved/expired alerts and
efficient look-ups by bbox, actor_id, alert_type, priority, and state.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass, field
from uuid import UUID

from marga_schemas.alert import Alert, AlertPriority, AlertState
from marga_schemas.common import GeoPoint


@dataclass
class AlertStore:
    """Thread-safe-ish in-memory alert store.

    Active alerts live in ``_active``; terminal-state alerts rotate
    through a bounded ``_history`` ring.
    """

    history_limit: int = 500

    _active: dict[UUID, Alert] = field(default_factory=dict)
    _history: deque[Alert] = field(default_factory=lambda: deque(maxlen=500))

    def __post_init__(self) -> None:
        self._history = deque(maxlen=self.history_limit)

    # ---- mutations --------------------------------------------------------

    def add(self, alert: Alert) -> Alert:
        """Insert or replace an alert.  Terminal alerts go to history."""
        if alert.state in {AlertState.RESOLVED, AlertState.EXPIRED, AlertState.SUPPRESSED}:
            self._active.pop(alert.alert_id, None)
            self._history.append(alert)
        else:
            self._active[alert.alert_id] = alert
        return alert

    def remove(self, alert_id: UUID) -> Alert | None:
        """Remove from active store; does not touch history."""
        return self._active.pop(alert_id, None)

    def update(self, alert: Alert) -> Alert:
        """Replace the stored copy.  Moves to history if state is terminal."""
        if alert.state in {AlertState.RESOLVED, AlertState.EXPIRED, AlertState.SUPPRESSED}:
            self._active.pop(alert.alert_id, None)
            self._history.append(alert)
        else:
            self._active[alert.alert_id] = alert
        return alert

    # ---- reads ------------------------------------------------------------

    def get(self, alert_id: UUID) -> Alert | None:
        alert = self._active.get(alert_id)
        if alert is not None:
            return alert
        # Check history (linear scan — acceptable for bounded buffer)
        for h in reversed(self._history):
            if h.alert_id == alert_id:
                return h
        return None

    def query(
        self,
        *,
        bbox: tuple[float, float, float, float] | None = None,
        actor_id: str | None = None,
        alert_type: str | None = None,
        state: AlertState | str | None = None,
        priority: AlertPriority | str | None = None,
        limit: int = 100,
    ) -> list[Alert]:
        """Query active alerts with optional filters.

        Parameters
        ----------
        bbox:
            ``(min_lat, min_lon, max_lat, max_lon)`` bounding box.
        actor_id:
            Filter to alerts that include this actor ID.
        alert_type:
            Exact match on ``alert_type`` string.
        state:
            Filter by alert state.
        priority:
            Filter by alert priority level.
        limit:
            Maximum results to return.
        """
        if isinstance(state, str):
            state = AlertState(state)
        if isinstance(priority, str):
            priority = AlertPriority(priority)

        results: list[Alert] = []
        source: Iterable[Alert] = self._active.values()

        # If querying for a terminal state, search history instead.
        if state in {AlertState.RESOLVED, AlertState.EXPIRED, AlertState.SUPPRESSED}:
            source = self._history

        for alert in source:
            if len(results) >= limit:
                break
            if state is not None and alert.state != state:
                continue
            if priority is not None and alert.priority != priority:
                continue
            if alert_type is not None and alert.alert_type != alert_type:
                continue
            if actor_id is not None and actor_id not in alert.affected_actor_ids:
                continue
            if bbox is not None and not self._in_bbox(alert.position, bbox):
                continue
            results.append(alert)

        return results

    def get_active_count(self) -> int:
        return len(self._active)

    def get_history(self, limit: int = 50) -> list[Alert]:
        """Return most recent history entries (newest first)."""
        items = list(self._history)
        items.reverse()
        return items[:limit]

    # ---- spatial helpers --------------------------------------------------

    @staticmethod
    def _in_bbox(
        pos: GeoPoint | None,
        bbox: tuple[float, float, float, float],
    ) -> bool:
        """Check whether *pos* falls within *bbox* (min_lat, min_lon, max_lat, max_lon)."""
        if pos is None:
            return False
        min_lat, min_lon, max_lat, max_lon = bbox
        return min_lat <= pos.lat <= max_lat and min_lon <= pos.lon <= max_lon
