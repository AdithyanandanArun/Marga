"""Edge V2X node — simulated OBU/ECU with local safety evaluation.

Each node represents an on-board unit (OBU) or edge computing unit (ECU)
in a vehicle.  It maintains:
    - actor state (VehicleState)
    - nearby peers (discovered via PC5 transport)
    - local risk evaluation (EdgeRiskEvaluator)
    - message priority (MessagePriority from marga_schemas)
    - transport (SimulatedPC5Transport or future real C-V2X)

When the internet is off, the node continues local PC5 safety delivery.
Cloud-only messages are queued or dropped, but safety-critical messages
are always delivered to nearby peers via direct PC5.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from marga_schemas.common import ConnectivityState, GeoPoint
from marga_schemas.messaging import MessagePriority, V2XMessage

from packages.schemas.canonical import RiskEvent, VehicleState

from .prioritizer import PrioritizationFactors, RiskPrioritizer
from .risk import EdgeRiskEvaluator
from .transport import SimulatedPC5Transport

logger = logging.getLogger(__name__)


class EdgeV2XNode:
    """A simulated OBU/ECU edge node with local V2X safety evaluation.

    The node receives actor state updates, discovers nearby peers via
    the PC5 transport, evaluates local risks, prioritises one active
    risk, and broadcasts safety-critical V2X messages to peers.

    Internet on/off:
        - Internet ON:  messages delivered via both PC5 and cloud
        - Internet OFF: cloud delivery removed, PC5 safety preserved
    """

    def __init__(
        self,
        actor_id: str,
        *,
        transport: SimulatedPC5Transport | None = None,
        risk_evaluator: EdgeRiskEvaluator | None = None,
        prioritizer: RiskPrioritizer | None = None,
    ) -> None:
        self.actor_id = actor_id
        self._transport = transport or SimulatedPC5Transport(actor_id)
        self._risk_evaluator = risk_evaluator or EdgeRiskEvaluator()
        self._prioritizer = prioritizer or RiskPrioritizer()

        self._state: VehicleState | None = None
        self._peers: dict[str, VehicleState] = {}
        self._active_risk: RiskEvent | None = None
        self._active_factors: PrioritizationFactors | None = None
        self._internet_available: bool = True
        self._received_messages: list[V2XMessage] = []
        self._subscription_id: str | None = None
        self._active_signature: tuple[str, tuple[str, ...]] | None = None
        self._message_listeners: list[Callable[[V2XMessage], Awaitable[None] | None]] = []
        self._risk_listeners: list[Callable[[RiskEvent, str], Awaitable[None] | None]] = []

    @property
    def transport(self) -> SimulatedPC5Transport:
        return self._transport

    @property
    def state(self) -> VehicleState | None:
        return self._state

    @property
    def active_risk(self) -> RiskEvent | None:
        return self._active_risk

    @property
    def active_factors(self) -> PrioritizationFactors | None:
        return self._active_factors

    @property
    def internet_available(self) -> bool:
        return self._internet_available

    def set_internet(self, available: bool) -> None:
        """Toggle internet availability.

        When internet is off, cloud delivery is removed but local PC5
        safety delivery continues.
        """
        self._internet_available = available
        if available:
            self._transport.set_connectivity(ConnectivityState.FULL)
        else:
            self._transport.set_connectivity(ConnectivityState.DIRECT_ONLY)
        logger.info("Node %s internet=%s", self.actor_id, available)

    def on_message(self, listener: Callable[[V2XMessage], Awaitable[None] | None]) -> None:
        """Observe a message that this node successfully delivered over PC5."""
        self._message_listeners.append(listener)

    def on_risk(self, listener: Callable[[RiskEvent, str], Awaitable[None] | None]) -> None:
        """Observe one newly activated local risk, not every reevaluation."""
        self._risk_listeners.append(listener)

    async def start(self) -> None:
        """Initialise the node: register receive handler."""
        self._subscription_id = await self._transport.receive(self._on_message)
        logger.info("Edge V2X node %s started", self.actor_id)

    async def stop(self) -> None:
        """Shut down the node."""
        if self._subscription_id:
            await self._transport.stop_receive(self._subscription_id)
        await self._transport.close()
        logger.info("Edge V2X node %s stopped", self.actor_id)

    def update_state(self, state: VehicleState) -> RiskEvent | None:
        """Update this node's actor state and re-evaluate risks.

        After updating state, the node:
        1. Updates its transport position for range calculations
        2. Evaluates local risks against all known peers
        3. Prioritises one active risk
        4. Broadcasts a safety message if a new risk is detected
        """
        self._state = state
        self._transport.update_position(state.position.lat, state.position.lon)

        # A direct peer state is valid only while that peer remains in PC5
        # range.  This prevents stale/out-of-range observations influencing a
        # local safety decision.
        return self.refresh_direct_peers()

    def refresh_direct_peers(self) -> RiskEvent | None:
        """Discard out-of-range peer observations and refresh local risk state."""
        in_range = set(self._transport.nearby_nodes())
        self._peers = {peer_id: peer for peer_id, peer in self._peers.items() if peer_id in in_range}
        return self._evaluate_risks()

    def update_peer_state(self, peer_state: VehicleState) -> RiskEvent | None:
        """Update a peer's state.  Called when a peer message is received."""
        if peer_state.actor_id not in self._transport.nearby_nodes():
            return None
        self._peers[peer_state.actor_id] = peer_state
        return self._evaluate_risks()

    def remove_peer(self, peer_id: str) -> None:
        """Remove a peer that has gone out of range or disconnected."""
        self._peers.pop(peer_id, None)

    def get_neighbours(self) -> list[str]:
        """Return IDs of peers currently within PC5 range."""
        return self._transport.nearby_nodes()

    def get_connectivity(self) -> ConnectivityState:
        """Return the current connectivity state."""
        return self._transport.transport_state().connectivity

    def get_link_state(self) -> dict[str, Any]:
        """Return full link state for the connectivity endpoint."""
        ls = self._transport.transport_state()
        return {
            "node_id": ls.node_id,
            "connectivity": ls.connectivity.value,
            "direct_peers": ls.direct_peers,
            "cloud_reachable": ls.cloud_reachable,
            "last_cloud_contact": ls.last_cloud_contact.isoformat() if ls.last_cloud_contact else None,
            "last_direct_contact": ls.last_direct_contact.isoformat() if ls.last_direct_contact else None,
            "internet_available": self._internet_available,
            "pc5_active": True,  # PC5 is always active for safety
        }

    def get_neighbour_details(self) -> list[dict[str, Any]]:
        """Return detailed neighbour information with link quality."""
        neighbours = []
        for peer_id in self._transport.nearby_nodes():
            quality = self._transport.link_quality(peer_id)
            peer_state = self._peers.get(peer_id)
            neighbours.append({
                "node_id": peer_id,
                "link_quality": round(quality, 4),
                "has_state": peer_state is not None,
                "actor_type": peer_state.actor_type.value if peer_state else None,
                "distance_m": self._transport.peer_distance_m(peer_id),
            })
        return neighbours

    async def broadcast_risk(self, risk: RiskEvent) -> bool:
        """Broadcast a safety-critical V2X message about a detected risk.

        The message is always sent via PC5 (regardless of internet state).
        If internet is available, it is also sent via cloud.
        """
        if self._state is None:
            return False

        pos = self._state.position
        message = V2XMessage(
            message_id=uuid.uuid4(),
            topic="risk.detected",
            priority=MessagePriority.CRITICAL_SAFETY,
            sender_id=self.actor_id,
            sender_position=GeoPoint(lat=pos.lat, lon=pos.lon, altitude_m=pos.altitude_m),
            timestamp=datetime.now(UTC),
            ttl_s=10,
            payload={
                "risk_id": risk.risk_id,
                "risk_type": risk.type.value,
                "severity": risk.severity,
                "confidence": risk.confidence,
                "ttc_s": risk.time_to_conflict_s,
                "affected_actor_ids": risk.affected_actor_ids,
                "policy_version": risk.policy_version,
            },
            audience_segment_ids=[self._state.road_segment_id] if self._state.road_segment_id else None,
            policy_version=risk.policy_version,
            provenance=["edge-v2x-node", "simulated-pc5"],
            evidence=risk.evidence,
        )
        delivered = await self._transport.send(message, internet_available=self._internet_available)
        if delivered:
            await self._emit_message(message)
        return delivered

    async def broadcast_state(self) -> bool:
        """Broadcast this node's actor state to nearby peers.

        State broadcasts use OPERATIONAL priority (not safety-critical)
        and are sent via PC5.  When internet is off, state broadcasts
        continue locally but cloud sync is skipped.
        """
        if self._state is None:
            return False

        pos = self._state.position
        message = V2XMessage(
            message_id=uuid.uuid4(),
            topic="actor.state.updated",
            priority=MessagePriority.OPERATIONAL,
            sender_id=self.actor_id,
            sender_position=GeoPoint(lat=pos.lat, lon=pos.lon, altitude_m=pos.altitude_m),
            timestamp=datetime.now(UTC),
            ttl_s=5,
            payload=self._state.model_dump(mode="json"),
            policy_version="edge-state-v1",
            provenance=["edge-v2x-node", "simulated-pc5"],
            evidence=[
                {
                    "type": "actor_state",
                    "position_uncertainty_m": self._state.position_uncertainty_m,
                    "source": self._state.source.value,
                }
            ],
        )
        delivered = await self._transport.send(message, internet_available=self._internet_available)
        if delivered:
            await self._emit_message(message)
        return delivered

    async def _on_message(self, message: V2XMessage) -> None:
        """Handle an incoming V2X message from a peer."""
        self._received_messages.append(message)

        if message.topic == "actor.state.updated":
            # Parse peer state from payload.
            try:
                payload = message.payload
                peer_state = VehicleState(**payload)
                activated = self.update_peer_state(peer_state)
                if activated is not None:
                    await self.broadcast_risk(activated)
                    await self._emit_risk(activated)
            except Exception:
                logger.debug("Failed to parse peer state from message %s", message.message_id)

        elif message.topic == "risk.detected":
            # Log received risk — the peer detected a conflict.
            logger.debug(
                "Node %s received risk from %s: %s",
                self.actor_id,
                message.sender_id,
                message.payload.get("risk_type"),
            )

    def _evaluate_risks(self) -> RiskEvent | None:
        """Refresh the local risk view and return only a new active conflict."""
        if self._state is None:
            return None
        risks = self._risk_evaluator.evaluate_all(self._state, list(self._peers.values()))
        active, factors = self._prioritizer.prioritize_with_factors(risks)
        signature = (
            (active.type.value, tuple(sorted(active.affected_actor_ids)))
            if active is not None else None
        )
        activated = active if active is not None and signature != self._active_signature else None
        self._active_risk = active
        self._active_factors = factors
        self._active_signature = signature
        if activated is not None:
            logger.info(
                "Node %s new active risk: %s (score=%.3f)",
                self.actor_id,
                activated.type.value,
                factors.composite_score if factors else 0,
            )
        return activated

    async def _emit_message(self, message: V2XMessage) -> None:
        for listener in self._message_listeners:
            result = listener(message)
            if result is not None:
                await result

    async def _emit_risk(self, risk: RiskEvent) -> None:
        for listener in self._risk_listeners:
            result = listener(risk, self.actor_id)
            if result is not None:
                await result

    @property
    def stats(self) -> dict[str, Any]:
        """Return node statistics for observability."""
        return {
            "actor_id": self.actor_id,
            "has_state": self._state is not None,
            "peer_count": len(self._peers),
            "neighbour_count": len(self.get_neighbours()),
            "internet_available": self._internet_available,
            "active_risk_type": self._active_risk.type.value if self._active_risk else None,
            "active_risk_score": self._active_factors.composite_score if self._active_factors else None,
            "transport_stats": self._transport.stats,
            "received_message_count": len(self._received_messages),
        }
