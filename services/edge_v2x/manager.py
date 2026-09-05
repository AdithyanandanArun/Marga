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
import inspect
import logging
from collections.abc import Awaitable, Callable
from typing import Any

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
        self._message_listeners: list[Callable[[V2XMessage], Awaitable[None] | None]] = []
        self._risk_listeners: list[Callable[[RiskEvent, str], Awaitable[None] | None]] = []

    @property
    def internet_available(self) -> bool:
        return self._internet_available

    @property
    def node_ids(self) -> list[str]:
        return list(self._nodes.keys())

    def on_message(self, listener: Callable[[V2XMessage], Awaitable[None] | None]) -> None:
        """Register a listener for V2X message events (for WebSocket streaming)."""
        if listener not in self._message_listeners:
            self._message_listeners.append(listener)

    def on_risk(self, listener: Callable[[RiskEvent, str], Awaitable[None] | None]) -> None:
        """Register a listener for risk creation events (for WebSocket streaming).

        The listener receives (risk_event, node_id).
        """
        if listener not in self._risk_listeners:
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
        node.on_message(self._emit_message)
        node.on_risk(self._emit_risk)
        await node.start()

        async with self._lock:
            # Connect the new node's transport to all existing nodes.
            for existing_node in self._nodes.values():
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

        This is the main entry point for the simulation loop.  State only
        reaches peers through in-range PC5 delivery; the manager never copies
        it directly into a remote node.  When a newly positioned node finds an
        in-range peer, that peer immediately republishes its latest state so
        both nodes can form a local safety view without waiting for a second
        simulator tick.

        Returns the active risk event if one was detected, else None.
        """
        node = self._nodes.get(state.actor_id)
        if node is None:
            logger.warning("Unknown actor %s, creating node", state.actor_id)
            node = await self.create_node(state.actor_id)

        activation = node.update_state(state)

        # The moved node can also leave another node's PC5 range. Prune those
        # remote local views immediately; stale state must not keep a risk
        # active until the other actor happens to publish again.
        for peer_node in self._nodes.values():
            if peer_node.actor_id != state.actor_id:
                peer_node.refresh_direct_peers()

        # PC5 neighbour-discovery handshake.  Only peers whose transport has
        # measured the new node in range can answer with their latest state.
        for peer_node in self._nodes.values():
            if peer_node.actor_id != state.actor_id and state.actor_id in peer_node.get_neighbours():
                await peer_node.broadcast_state()

        # Broadcast state to peers via PC5.
        await node.broadcast_state()

        # A local activation from the state update is sent once. Activations
        # caused by a received PC5 state are sent inside EdgeV2XNode's receive
        # handler and observed through the same listener path.
        if activation is not None:
            await node.broadcast_risk(activation)
            await self._emit_risk(activation, state.actor_id)

        return node.active_risk

    async def _emit_message(self, message: V2XMessage) -> None:
        for listener in self._message_listeners:
            try:
                result = listener(message)
                if inspect.isawaitable(result):
                    await result
            except Exception:
                logger.exception("V2X message listener error")

    async def _emit_risk(self, risk: RiskEvent, node_id: str) -> None:
        for listener in self._risk_listeners:
            try:
                result = listener(risk, node_id)
                if inspect.isawaitable(result):
                    await result
            except Exception:
                logger.exception("Risk listener error")

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
