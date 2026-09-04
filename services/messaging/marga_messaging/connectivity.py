"""Connectivity state tracking — monitors link health and emits transition events."""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from collections.abc import Callable
from datetime import UTC, datetime

from marga_schemas.common import ConnectivityState
from marga_schemas.messaging import LinkState

logger = logging.getLogger(__name__)

# Number of recent delivery results to track per route for state inference.
_WINDOW_SIZE = 20

# Thresholds for state transitions based on success rate within the window.
_THRESHOLD_FULL = 0.9  # >= 90% success across all routes -> FULL
_THRESHOLD_INTERMITTENT = 0.3  # >= 30% success on any route -> INTERMITTENT
# Below 30% on all routes -> ISOLATED
# Cloud down but direct succeeding -> DIRECT_ONLY


class ConnectivityMonitor:
    """Track link state based on actual delivery outcomes.

    Reports delivery success/failure per route (e.g., "cloud", "direct"),
    maintains a sliding window, and infers connectivity state. Emits
    ``connectivity.changed`` events on transitions via registered listeners.
    """

    def __init__(self, node_id: str) -> None:
        self._node_id = node_id
        self._state = ConnectivityState.FULL
        self._route_windows: dict[str, deque[bool]] = {}
        self._listeners: list[Callable] = []
        self._lock = asyncio.Lock()
        self._last_cloud_contact: datetime | None = None
        self._last_direct_contact: datetime | None = None

    @property
    def state(self) -> ConnectivityState:
        return self._state

    def on_transition(self, listener: Callable) -> None:
        """Register a callback for connectivity state transitions.

        The callback receives (old_state, new_state) as arguments.
        """
        self._listeners.append(listener)

    async def report_delivery(self, success: bool, route: str) -> None:
        """Update connectivity state based on a delivery outcome.

        Args:
            success: Whether the delivery attempt succeeded.
            route: The route used, e.g. "cloud" or "direct".
        """
        async with self._lock:
            if route not in self._route_windows:
                self._route_windows[route] = deque(maxlen=_WINDOW_SIZE)
            self._route_windows[route].append(success)

            if success:
                now = datetime.now(UTC)
                if route == "cloud":
                    self._last_cloud_contact = now
                elif route == "direct":
                    self._last_direct_contact = now

            new_state = self._infer_state()
            if new_state != self._state:
                old_state = self._state
                self._state = new_state
                logger.info(
                    "Connectivity transition: %s -> %s",
                    old_state.value,
                    new_state.value,
                )
                await self._emit_transition(old_state, new_state)

    def _success_rate(self, route: str) -> float:
        """Compute success rate for a route's sliding window."""
        window = self._route_windows.get(route)
        if not window:
            return 0.0
        return sum(1 for ok in window if ok) / len(window)

    def _infer_state(self) -> ConnectivityState:
        """Determine connectivity state from delivery history."""
        cloud_rate = self._success_rate("cloud")
        direct_rate = self._success_rate("direct")

        cloud_window = self._route_windows.get("cloud")
        direct_window = self._route_windows.get("direct")

        # If we have no data at all, assume FULL (optimistic start).
        if not cloud_window and not direct_window:
            return ConnectivityState.FULL

        # Both routes healthy -> FULL
        if cloud_rate >= _THRESHOLD_FULL and (not direct_window or direct_rate >= _THRESHOLD_FULL):
            return ConnectivityState.FULL

        # Cloud-only healthy -> FULL (cloud is sufficient)
        if cloud_rate >= _THRESHOLD_FULL:
            return ConnectivityState.FULL

        # Cloud down but direct working -> DIRECT_ONLY
        if cloud_rate < _THRESHOLD_INTERMITTENT and direct_rate >= _THRESHOLD_FULL:
            return ConnectivityState.DIRECT_ONLY

        # Partial success on any route -> INTERMITTENT
        if cloud_rate >= _THRESHOLD_INTERMITTENT or direct_rate >= _THRESHOLD_INTERMITTENT:
            return ConnectivityState.INTERMITTENT

        # Everything failing -> ISOLATED
        return ConnectivityState.ISOLATED

    async def _emit_transition(self, old_state: ConnectivityState, new_state: ConnectivityState) -> None:
        """Notify registered listeners about a state transition."""
        for listener in self._listeners:
            try:
                result = listener(old_state, new_state)
                if asyncio.iscoroutine(result):
                    await result
            except Exception:
                logger.exception("Error in connectivity transition listener")

    def get_state(self) -> ConnectivityState:
        """Return the current connectivity state."""
        return self._state

    def get_link_state(self) -> LinkState:
        """Return a full LinkState snapshot."""
        return LinkState(
            node_id=self._node_id,
            connectivity=self._state,
            cloud_reachable=self._success_rate("cloud") >= _THRESHOLD_INTERMITTENT,
            last_cloud_contact=self._last_cloud_contact,
            last_direct_contact=self._last_direct_contact,
            queue_depth={},
        )
