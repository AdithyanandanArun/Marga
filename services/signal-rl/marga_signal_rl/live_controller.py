"""Live, graph-driven adaptive-signal control.

This module is deliberately separate from the mock training environment. It
turns canonical graph and signal telemetry into an RL observation, runs the
saved policy through the deterministic safety envelope, and records an
explainable decision. Applying a decision is delegated to an injected adapter
command function, so replacing SUMO with another traffic-light controller does
not alter decision logic.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from datetime import UTC, datetime
from statistics import fmean

from packages.schemas.canonical import TrafficSignalState
from packages.schemas.signal_control import SignalControlDecision, SignalJunctionTopology
from services.mobility_graph.service import MobilityGraphService

from .actions import ACTION_EXTENSION_S, SignalAction
from .agent import POLICY_VERSION, TabularQLearningAgent
from .safety import SignalSafetyController
from .state import ApproachState, SignalObservation

SignalCommandExecutor = Callable[[dict[str, object]], None]


class SignalControlError(RuntimeError):
    """Raised when a requested live-signal operation lacks valid evidence."""


class LiveSignalController:
    """Build safe RL decisions from registered topology and live graph state."""

    def __init__(
        self,
        graph: MobilityGraphService,
        agent: TabularQLearningAgent,
        safety: SignalSafetyController | None = None,
        *,
        decision_limit: int = 10_000,
    ) -> None:
        self._graph = graph
        self._agent = agent
        self._agent.epsilon = 0.0
        self._safety = safety or SignalSafetyController()
        self._topologies: dict[str, SignalJunctionTopology] = {}
        self._signal_states: dict[str, TrafficSignalState] = {}
        self._phase_started_at: dict[str, datetime] = {}
        self._last_phase: dict[str, str] = {}
        self._executor: SignalCommandExecutor | None = None
        self._decisions: dict[str, SignalControlDecision] = {}
        self._decision_order: deque[str] = deque(maxlen=decision_limit)

    def clear(self) -> None:
        """Clear volatile topology, telemetry and decision state (test/run reset)."""
        self._topologies.clear()
        self._signal_states.clear()
        self._phase_started_at.clear()
        self._last_phase.clear()
        self._decisions.clear()
        self._decision_order.clear()
        self._executor = None

    def register_topology(self, topology: SignalJunctionTopology) -> SignalJunctionTopology:
        """Register topology supplied by a map/SUMO adapter, never guessed here."""
        if len({item.movement_id for item in topology.approaches}) != len(topology.approaches):
            raise SignalControlError("every approach movement_id must be unique")
        self._topologies[topology.junction_id] = topology
        return topology

    def register_executor(self, executor: SignalCommandExecutor | None) -> None:
        """Attach the current simulator/RSU command sink, or detach it safely."""
        self._executor = executor

    def observe_signal(self, state: TrafficSignalState) -> None:
        """Store the canonical phase telemetry used by the next graph decision."""
        self._signal_states[state.intersection_id] = state
        previous = self._last_phase.get(state.intersection_id)
        if previous != state.current_phase:
            self._phase_started_at[state.intersection_id] = state.ts
            self._last_phase[state.intersection_id] = state.current_phase

    def topology(self, junction_id: str) -> SignalJunctionTopology | None:
        return self._topologies.get(junction_id)

    @property
    def junction_ids(self) -> tuple[str, ...]:
        """Configured junctions, in deterministic order for controller loops."""
        return tuple(sorted(self._topologies))

    @property
    def executor_registered(self) -> bool:
        return self._executor is not None

    def state(self, junction_id: str) -> SignalObservation:
        topology = self._require_topology(junction_id)
        signal = self._signal_states.get(junction_id)
        if signal is None:
            raise SignalControlError(f"no canonical signal telemetry for junction {junction_id!r}")
        return self._observation(topology, signal)

    def recommend(
        self, junction_id: str, requested_action: SignalAction | None = None
    ) -> SignalControlDecision:
        """Create and persist a safe recommendation; does not change the signal."""
        topology = self._require_topology(junction_id)
        observation = self.state(junction_id)
        recommendation = self._agent.greedy_recommend(observation)
        requested = requested_action or SignalAction(recommendation["action"])
        verdict = self._safety.validate(observation, requested)
        confidence, evidence = self._decision_evidence(topology, observation)
        decision = SignalControlDecision(
            junction_id=junction_id,
            signal_id=topology.signal_id,
            requested_action=requested.value,
            effective_action=verdict.action.value,
            safety_override=verdict.safety_override,
            safety_reason=verdict.reason,
            confidence=confidence,
            policy_version=POLICY_VERSION,
            evidence=[
                *evidence,
                {"type": "q_values", "value": recommendation["q_values"]},
                {"type": "policy_proposal", "value": recommendation["action"]},
            ],
            provenance=["live-mobility-graph", "tabular-q-learning", "signal-safety-controller"],
        )
        self._record(decision)
        return decision

    def apply(self, decision_id: str) -> SignalControlDecision:
        """Apply a recorded, safety-validated decision through the registered adapter."""
        decision = self._decisions.get(decision_id)
        if decision is None:
            raise SignalControlError(f"unknown decision {decision_id!r}")
        if decision.applied or decision.application_error is not None:
            return decision
        if decision.effective_action == SignalAction.HOLD.value:
            updated = decision.model_copy(update={"applied": True})
            self._decisions[decision_id] = updated
            return updated
        if self._executor is None:
            updated = decision.model_copy(update={"application_error": "no signal command executor registered"})
            self._decisions[decision_id] = updated
            return updated

        topology = self._require_topology(decision.junction_id)
        try:
            self._executor(self._adapter_command(topology, decision))
        except Exception as exc:  # adapter failures must remain visible and replayable
            updated = decision.model_copy(update={"application_error": str(exc)})
        else:
            updated = decision.model_copy(update={"applied": True})
        self._decisions[decision_id] = updated
        return updated

    def decide_and_apply(self, junction_id: str) -> SignalControlDecision:
        """Single safe control tick used by a live simulation/RSU loop."""
        return self.apply(self.recommend(junction_id).decision_id)

    def get_decision(self, decision_id: str) -> SignalControlDecision | None:
        return self._decisions.get(decision_id)

    def latest_decision(self, junction_id: str) -> SignalControlDecision | None:
        for decision_id in reversed(self._decision_order):
            decision = self._decisions[decision_id]
            if decision.junction_id == junction_id:
                return decision
        return None

    def _require_topology(self, junction_id: str) -> SignalJunctionTopology:
        topology = self._topologies.get(junction_id)
        if topology is None:
            raise SignalControlError(f"no topology registered for junction {junction_id!r}")
        return topology

    def _observation(self, topology: SignalJunctionTopology, signal: TrafficSignalState) -> SignalObservation:
        approaches: dict[str, ApproachState] = {}
        for approach in topology.approaches:
            edges = [self._graph.get_edge(edge_id) for edge_id in approach.incoming_edge_ids]
            known_edges = [edge for edge in edges if edge is not None]
            if not known_edges:
                raise SignalControlError(
                    f"no live graph evidence for approach {approach.movement_id!r} at {topology.junction_id!r}"
                )
            vehicle_count = sum(edge.vehicle_count for edge in known_edges)
            pedestrian_count = sum(edge.pedestrian_count for edge in known_edges)
            vru_count = pedestrian_count + sum(
                round(edge.vehicle_count * edge.two_wheeler_ratio) for edge in known_edges
            )
            downstream = [self._graph.get_edge(edge_id) for edge_id in approach.downstream_edge_ids]
            downstream_edges = [edge for edge in downstream if edge is not None]
            approaches[approach.movement_id] = ApproachState(
                movement_id=approach.movement_id,
                queue_length=sum(edge.queue_length for edge in known_edges),
                density=fmean(edge.density for edge in known_edges),
                avg_speed_mps=(
                    sum(edge.avg_speed_mps * edge.vehicle_count for edge in known_edges) / vehicle_count
                    if vehicle_count else 0.0
                ),
                incoming_flow=sum(edge.flow_rate_vph for edge in known_edges) / 60.0,
                downstream_occupancy=(
                    fmean(edge.occupancy for edge in downstream_edges)
                    if downstream_edges else fmean(edge.downstream_congestion for edge in known_edges)
                ),
                pedestrian_demand=pedestrian_count,
                vru_density=vru_count / approach.approach_length_m,
            )
        phase_started = self._phase_started_at.get(topology.junction_id, signal.ts)
        elapsed = max(0.0, (signal.ts - phase_started).total_seconds())
        remaining = signal.phase_remaining_s
        if remaining is None:
            remaining = max(0.0, topology.default_phase_duration_s - elapsed)
        return SignalObservation(
            junction_id=topology.junction_id,
            current_phase=signal.current_phase,
            phase_index=topology.phase_index_by_name.get(signal.current_phase, 0),
            phase_elapsed_s=elapsed,
            phase_remaining_s=remaining,
            approaches=approaches,
            ts=signal.ts.astimezone(UTC).isoformat(),
        )

    def _decision_evidence(
        self, topology: SignalJunctionTopology, observation: SignalObservation
    ) -> tuple[float, list[dict[str, object]]]:
        edge_states = [
            self._graph.get_edge(edge_id)
            for approach in topology.approaches
            for edge_id in approach.incoming_edge_ids
        ]
        known = [edge for edge in edge_states if edge is not None]
        confidence = fmean(edge.confidence for edge in known) if known else 0.0
        return confidence, [
            {"type": "junction_queue", "value": observation.total_queue()},
            {"type": "junction_avg_speed_mps", "value": observation.avg_speed()},
            {"type": "pedestrian_demand", "value": observation.total_ped_demand()},
            {"type": "graph_edge_ids", "value": [edge.edge_id for edge in known]},
            {"type": "graph_confidence", "value": confidence},
        ]

    def _adapter_command(
        self, topology: SignalJunctionTopology, decision: SignalControlDecision
    ) -> dict[str, object]:
        action = SignalAction(decision.effective_action)
        if action is SignalAction.NEXT_PHASE:
            observation = self.state(topology.junction_id)
            return {
                "signal_id": topology.signal_id,
                "action": action.value,
                "phase_index": (observation.phase_index + 1) % topology.phase_count,
            }
        observation = self.state(topology.junction_id)
        return {
            "signal_id": topology.signal_id,
            "action": action.value,
            "duration_s": observation.phase_remaining_s + ACTION_EXTENSION_S[action],
        }

    def _record(self, decision: SignalControlDecision) -> None:
        if decision.decision_id not in self._decisions and len(self._decision_order) == self._decision_order.maxlen:
            expired = self._decision_order.popleft()
            self._decisions.pop(expired, None)
        if decision.decision_id not in self._decisions:
            self._decision_order.append(decision.decision_id)
        self._decisions[decision.decision_id] = decision
