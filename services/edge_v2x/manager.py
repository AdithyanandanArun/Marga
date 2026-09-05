"""Edge V2X node manager — coordinates all simulated OBU/ECU nodes.

The manager is responsible for:
    - Creating and registering edge nodes
    - Connecting peers (registering transports with each other)
    - Toggling internet availability globally
    - Routing state updates between nodes
    - Collecting risk events for WebSocket streaming

It provides the backing state for the FastAPI endpoints:
    WS  v2x.message
    WS  risk.created
    GET /nodes/:id/neighbours
    GET /nodes/:id/connectivity
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from marga_schemas.common import ConnectivityState
from marga_schemas.messaging import V2XMessage
from packages.schemas.canonical import RiskEvent, VehicleState

from .node import EdgeV2XNode
from .prioritizer import RiskPrioritizer
from .risk import EdgeRiskEvaluator
from .transport import SimulatedPC5Transport

logger = logging.getLogger(__name__)


class EdgeV2XManager:
    """Manages all edge V2X nodes in the simulation.

    The manager creates nodes, connects their transports for peer-to-peer
    PC5 communication, and provides a query/update API for the FastAPI
    endpoints.
    """

    def __init__(
        self,
        *,
        pc5_range_m: float = 300.0,
    ) -> None:
        self._pc5_range_m = pc5_range_m
        self._nodes: dict[str, EdgeV2XNode] = {}
        self._internet_available: bool = True
        self._lock = asyncio.Lock()

        # Event listeners for WebSocket streaming
        self._message_listeners: list[Callable[[V2XMessage], None]] = []
        self._risk_listeners: list[Callable[[RiskEvent, str], None]] = []

    @property
    def internet_available(self) -> bool:
        return self._internet_available

    @property
    def node_ids(self) -> list[str]:
        return list(self._nodes.keys())

    def on_message(self, listener: Callable[[V2XMessage], None]) -> None:
        """Register a listener for V2X message events (for WebSocket streaming)."""
        self._message_listeners.append(listener)

    def on_risk(self, listener: Callable[[RiskEvent, str], None]) -> None:
        """Register a listener for risk creation events (for WebSocket streaming).

        The listener receives (risk_event, node_id).
        """
        self._risk_listeners.append(listener)

    async def create_node(
        self,
        actor_id: str,
        *,
        evaluator: EdgeRiskEvaluator | None = None,
        prioritizer: RiskPrioritizer | None = None,
    ) -> EdgeV2XNode:
        """Create and register a new edge V2X node.

        The node's transport is automatically connected to all existing
        nodes for peer-to-peer PC5 communication.
        """
        transport = SimulatedPC5Transport(actor_id, pc5_range_m=self._pc5_range_m)
        node = EdgeV2XNode(
            actor_id,
            transport=transport,
            risk_evaluator=evaluator,
            prioritizer=prioritizer,
        )
        await node.start()

        async with self._lock:
            # Connect the new node's transport to all existing nodes.
            for existing_id, existing_node in self._nodes.items():
                node.transport.register_peer(existing_node.transport)
                existing_node.transport.register_peer(node.transport)

            self._nodes[actor_id] = node

        # Set initial internet state
        node.set_internet(self._internet_available)

        logger.info("Created edge V2X node %s (%d total)", actor_id, len(self._nodes))
        return node

    async def remove_node(self, actor_id: str) -> None:
        """Remove a node and unregister its transport from all peers."""
        async with self._lock:
            node = self._nodes.pop(actor_id, None)
            if node is None:
                return
            for existing_node in self._nodes.values():
                existing_node.transport.unregister_peer(actor_id)
                node.transport.unregister_peer(existing_node.actor_id)

        await node.stop()
        logger.info("Removed edge V2X node %s", actor_id)

    def get_node(self, actor_id: str) -> EdgeV2XNode | None:
        """Get a node by actor ID."""
        return self._nodes.get(actor_id)

    def get_all_nodes(self) -> list[EdgeV2XNode]:
        """Return all registered nodes."""
        return list(self._nodes.values())

    async def update_actor_state(self, state: VehicleState) -> RiskEvent | None:
        """Update an actor's state and trigger risk evaluation.

        This is the main entry point for the simulation loop.  When a
        vehicle's state changes, this method:
        1. Updates the node's state
        2. Pushes the state to all peer nodes (so they know about this actor)
        3. Re-evaluates local risks (including newly learned peer states)
        4. If a new risk is detected, broadcasts it via PC5
        5. Notifies risk listeners (for WebSocket streaming)

        Returns the active risk event if one was detected, else None.
        """
        node = self._nodes.get(state.actor_id)
        if node is None:
            logger.warning("Unknown actor %s, creating node", state.actor_id)
            node = await self.create_node(state.actor_id)

        # Push this state to all peer nodes so they can evaluate risks.
        for peer_node in self._nodes.values():
            if peer_node.actor_id != state.actor_id:
                peer_node.update_peer_state(state)

        old_risk_id = node.active_risk.risk_id if node.active_risk else None
        node.update_state(state)

        # Broadcast state to peers via PC5.
        await node.broadcast_state()

        # If a new risk was detected, broadcast it.
        active_risk = node.active_risk
        if active_risk and active_risk.risk_id != old_risk_id:
            await node.broadcast_risk(active_risk)
            # Notify risk listeners.
            for listener in self._risk_listeners:
                try:
                    listener(active_risk, state.actor_id)
                except Exception:
                    logger.exception("Risk listener error")

        return active_risk

    def set_internet(self, available: bool) -> None:
        """Toggle internet availability for all nodes.

        When internet is off:
        - Cloud delivery is removed
        - Local PC5 safety delivery continues
        - Connectivity state transitions to DIRECT_ONLY
        """
        self._internet_available = available
        for node in self._nodes.values():
            node.set_internet(available)
        logger.info("Internet set to %s for all %d nodes", available, len(self._nodes))

    def get_neighbours(self, actor_id: str) -> list[dict[str, Any]] | None:
        """Get neighbour details for a node."""
        node = self._nodes.get(actor_id)
        if node is None:
            return None
        return node.get_neighbour_details()

    def get_connectivity(self, actor_id: str) -> dict[str, Any] | None:
        """Get connectivity state for a node."""
        node = self._nodes.get(actor_id)
        if node is None:
            return None
        return node.get_link_state()

    def get_all_risks(self) -> list[dict[str, Any]]:
        """Return all active risks across all nodes."""
        risks: list[dict[str, Any]] = []
        for node in self._nodes.values():
            if node.active_risk is not None:
                risk = node.active_risk
                risks.append({
                    "node_id": node.actor_id,
                    "risk_id": risk.risk_id,
                    "risk_type": risk.type.value,
                    "severity": risk.severity,
                    "confidence": risk.confidence,
                    "risk_score": risk.risk_score,
                    "ttc_s": risk.time_to_conflict_s,
                    "affected_actor_ids": risk.affected_actor_ids,
                    "policy_version": risk.policy_version,
                    "ts": risk.ts.isoformat(),
                })
        return risks

    def get_all_stats(self) -> dict[str, Any]:
        """Return statistics for all nodes."""
        return {
            "node_count": len(self._nodes),
            "internet_available": self._internet_available,
            "nodes": {nid: node.stats for nid, node in self._nodes.items()},
        }

    async def shutdown(self) -> None:
        """Shut down all nodes."""
        for node in list(self._nodes.values()):
            await node.stop()
        self._nodes.clear()
        logger.info("All edge V2X nodes shut down")
