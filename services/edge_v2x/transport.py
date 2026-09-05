"""Transport-neutral V2X edge communication interface.

Defines the protocol that every edge V2X transport (OBU/ECU-level) must
implement.  The interface is deliberately distinct from the broker-level
``V2XTransport`` in ``services.messaging``: that protocol handles cloud
pub/sub, while this one handles direct peer-to-peer safety delivery between
road users.

The canonical methods are:
    send         — transmit a V2X message to nearby peers
    receive      — register a handler for incoming messages
    nearbyNodes  — list peers currently within communication range
    linkQuality  — per-peer link quality metric [0, 1]
    transportState — current connectivity and operational state

``SimulatedPC5Transport`` implements this interface for the hackathon.
A future real C-V2X PC5 transport can replace it without changing any
caller, preserving simulation-reality parity.
"""

from __future__ import annotations

import asyncio
import logging
import math
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

from marga_schemas.common import ConnectivityState, GeoPoint
from marga_schemas.messaging import LinkState, V2XMessage
from packages.geo.coordinates import distance_m

logger = logging.getLogger(__name__)

# Default PC5 direct communication range in metres.
# Real C-V2X PC5 typically operates at 300-1000 m line-of-sight.
DEFAULT_PC5_RANGE_M = 300.0

# Distance at which link quality drops to zero (metres).
_LINK_QUALITY_ZERO_M = 400.0


@runtime_checkable
class EdgeV2XTransport(Protocol):
    """Transport-neutral V2X edge communication protocol.

    Every simulated OBU/ECU uses this interface.  A real C-V2X transport
    will implement the same methods without any caller changes.
    """

    @property
    def node_id(self) -> str:
        """Unique identifier of this transport node."""
        ...

    async def send(self, message: V2XMessage, *, internet_available: bool) -> bool:
        """Transmit a V2X message to nearby peers via direct PC5.

        When ``internet_available`` is False, cloud delivery is skipped
        but local PC5 delivery continues for safety-critical messages.

        Returns True if the message was delivered to at least one peer.
        """
        ...

    async def receive(self, handler: Callable[[V2XMessage], None]) -> str:
        """Register a handler for incoming V2X messages.

        Returns a subscription ID that can be used with ``stop_receive``.
        """
        ...

    async def stop_receive(self, subscription_id: str) -> None:
        """Remove a receive handler by subscription ID."""
        ...

    def nearby_nodes(self) -> list[str]:
        """Return IDs of peers currently within PC5 communication range."""
        ...

    def link_quality(self, peer_id: str) -> float:
        """Return link quality [0, 1] for a specific peer.

        Quality decreases with distance.  Returns 0 for out-of-range peers.
        """
        ...

    def transport_state(self) -> LinkState:
        """Return the current transport connectivity state."""
        ...

    async def close(self) -> None:
        """Gracefully shut down the transport."""
        ...


def _link_quality_from_distance(dist_m: float, range_m: float) -> float:
    """Compute link quality [0, 1] from distance.

    Uses a linear falloff from 1.0 at 0 m to 0.0 at ``_LINK_QUALITY_ZERO_M``.
    Beyond the effective range, quality is 0.
    """
    if dist_m <= 0:
        return 1.0
    if dist_m >= _LINK_QUALITY_ZERO_M:
        return 0.0
    return max(0.0, 1.0 - dist_m / _LINK_QUALITY_ZERO_M)


class SimulatedPC5Transport:
    """Simulated PC5 direct V2X transport for edge nodes.

    Models direct device-to-device communication with:
    - Distance-limited range (default 300 m)
    - Link quality based on distance
    - Internet on/off: cloud delivery removed when offline, PC5 preserved
    - In-process message delivery for the hackathon simulation

    This is NOT a network simulator.  It models the *delivery semantics*
    of PC5: who can talk to whom, with what quality, and what happens when
    the internet drops.  A real C-V2X stack replaces this class with the
    same interface.
    """

    def __init__(
        self,
        node_id: str,
        *,
        pc5_range_m: float = DEFAULT_PC5_RANGE_M,
    ) -> None:
        self._node_id = node_id
        self._pc5_range_m = pc5_range_m
        self._handlers: dict[str, Callable[[V2XMessage], None]] = {}
        self._lock = asyncio.Lock()
        self._closed = False
        self._send_count = 0
        self._deliver_count = 0

        # Position and peer registry are managed by the NodeManager, which
        # calls ``update_position`` and ``register_peer`` / ``unregister_peer``.
        self._lat: float | None = None
        self._lon: float | None = None
        self._peers: dict[str, SimulatedPC5Transport] = {}

        # Connectivity state
        self._connectivity: ConnectivityState = ConnectivityState.FULL
        self._last_cloud_contact: datetime | None = None
        self._last_direct_contact: datetime | None = None

    @property
    def node_id(self) -> str:
        return self._node_id

    def update_position(self, lat: float, lon: float) -> None:
        """Update this node's geographic position for range calculations."""
        self._lat = lat
        self._lon = lon

    def register_peer(self, peer: SimulatedPC5Transport) -> None:
        """Register a peer transport for direct PC5 delivery."""
        self._peers[peer.node_id] = peer

    def unregister_peer(self, peer_id: str) -> None:
        """Remove a peer transport."""
        self._peers.pop(peer_id, None)

    def set_connectivity(self, state: ConnectivityState) -> None:
        """Set the connectivity state (e.g. DIRECT_ONLY when internet is off)."""
        self._connectivity = state
        if state == ConnectivityState.FULL:
            self._last_cloud_contact = datetime.now(UTC)

    def _peer_distance(self, peer: SimulatedPC5Transport) -> float | None:
        """Compute distance to a peer in metres, or None if position unknown."""
        if self._lat is None or self._lon is None:
            return None
        if peer._lat is None or peer._lon is None:
            return None
        return distance_m(
            GeoPoint(lat=self._lat, lon=self._lon),
            GeoPoint(lat=peer._lat, lon=peer._lon),
        )

    def _is_in_range(self, peer: SimulatedPC5Transport) -> bool:
        """Check if a peer is within PC5 communication range."""
        dist = self._peer_distance(peer)
        if dist is None:
            return False
        return dist <= self._pc5_range_m

    async def send(self, message: V2XMessage, *, internet_available: bool) -> bool:
        """Send a V2X message to nearby peers via simulated PC5.

        When ``internet_available`` is False, only local PC5 delivery occurs.
        Safety-critical messages (CRITICAL_SAFETY priority) are always
        delivered via PC5 regardless of internet state.
        """
        if self._closed:
            return False

        self._send_count += 1
        delivered = False

        async with self._lock:
            peers = list(self._peers.values())

        for peer in peers:
            if not self._is_in_range(peer):
                continue
            # Deliver to peer's handlers
            async with peer._lock:
                handlers = list(peer._handlers.values())
            for handler in handlers:
                try:
                    result = handler(message)
                    if asyncio.iscoroutine(result):
                        await result
                    delivered = True
                    self._deliver_count += 1
                    self._last_direct_contact = datetime.now(UTC)
                except Exception:
                    logger.exception("Handler error delivering to %s", peer.node_id)

        if internet_available:
            self._last_cloud_contact = datetime.now(UTC)

        return delivered

    async def receive(self, handler: Callable[[V2XMessage], None]) -> str:
        """Register a handler for incoming V2X messages."""
        sub_id = str(uuid.uuid4())
        async with self._lock:
            self._handlers[sub_id] = handler
        return sub_id

    async def stop_receive(self, subscription_id: str) -> None:
        """Remove a receive handler by subscription ID."""
        async with self._lock:
            self._handlers.pop(subscription_id, None)

    def nearby_nodes(self) -> list[str]:
        """Return IDs of peers currently within PC5 communication range."""
        result: list[str] = []
        for peer_id, peer in self._peers.items():
            if self._is_in_range(peer):
                result.append(peer_id)
        return result

    def link_quality(self, peer_id: str) -> float:
        """Return link quality [0, 1] for a specific peer."""
        peer = self._peers.get(peer_id)
        if peer is None:
            return 0.0
        dist = self._peer_distance(peer)
        if dist is None:
            return 0.0
        return _link_quality_from_distance(dist, self._pc5_range_m)

    def transport_state(self) -> LinkState:
        """Return the current transport connectivity state."""
        return LinkState(
            node_id=self._node_id,
            connectivity=self._connectivity,
            direct_peers=len(self.nearby_nodes()),
            cloud_reachable=self._connectivity in (ConnectivityState.FULL, ConnectivityState.INTERMITTENT),
            last_cloud_contact=self._last_cloud_contact,
            last_direct_contact=self._last_direct_contact,
            queue_depth={},
        )

    async def close(self) -> None:
        """Gracefully shut down the transport."""
        self._closed = True
        async with self._lock:
            self._handlers.clear()

    @property
    def stats(self) -> dict[str, int]:
        """Return transport statistics for observability."""
        return {
            "send_count": self._send_count,
            "deliver_count": self._deliver_count,
            "peer_count": len(self._peers),
            "in_range_count": len(self.nearby_nodes()),
        }
