"""Gateway API for graph-driven, safety-constrained adaptive signals."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, cast

from fastapi import APIRouter, HTTPException
from marga_signal_rl.actions import SignalAction
from marga_signal_rl.agent import TabularQLearningAgent
from marga_signal_rl.live_controller import LiveSignalController, SignalControlError
from marga_signal_rl.trainer import POLICY_PATH
from pydantic import BaseModel

from packages.schemas.signal_control import SignalControlDecision, SignalJunctionTopology
from services.mobility_graph import mobility_graph

router = APIRouter(prefix="/v1/signals", tags=["signal-control"])


def _load_policy() -> TabularQLearningAgent:
    """Load the committed trained policy; never retrain silently in production."""
    if not POLICY_PATH.exists():
        raise RuntimeError(f"signal policy is missing: {POLICY_PATH}")
    agent = TabularQLearningAgent.load(POLICY_PATH)
    agent.epsilon = 0.0
    return agent


signal_controller = LiveSignalController(mobility_graph, _load_policy())


def register_signal_executor(executor: Callable[[dict[str, object]], None] | None) -> None:
    """Connect a SimulationAdapter/RSU command sink to the live controller."""
    signal_controller.register_executor(executor)


# ---------------------------------------------------------------------------
# Applied-action delivery to a simulator that cannot be called synchronously.
#
# The browser junction simulator is not reachable from the gateway, so applied
# RL actions are queued here and drained by the simulator on its own tick. A
# bounded queue keeps a disconnected simulator from growing memory without
# limit, and draining is destructive so one action is never applied twice.
# ---------------------------------------------------------------------------

_MAX_PENDING_COMMANDS = 256
_pending_commands: list[dict[str, Any]] = []
_command_history: list[dict[str, Any]] = []


def _queue_command(command: dict[str, object]) -> None:
    _pending_commands.append({**command, "issued_at": datetime.now(UTC).isoformat()})
    if command.get("action") != "HOLD":
        _command_history.append(dict(_pending_commands[-1]))
        del _command_history[:-10]
    if len(_pending_commands) > _MAX_PENDING_COMMANDS:
        del _pending_commands[:-_MAX_PENDING_COMMANDS]


def pending_command_count() -> int:
    return len(_pending_commands)


def reset_pending_commands() -> None:
    _pending_commands.clear()
    _command_history.clear()


# An applied action must reach the simulator by default; without this the
# controller reports "no signal command executor registered" and RL decisions
# never change a real phase.
signal_controller.register_executor(_queue_command)


class RecommendRequest(BaseModel):
    apply: bool = False


class ApplyRequest(BaseModel):
    decision_id: str | None = None
    action: str | None = None


def _recommendation_payload(decision: SignalControlDecision) -> dict[str, Any]:
    """Keep the dashboard contract compact while retaining the full trace."""
    return {
        "junction_id": decision.junction_id,
        "action": decision.effective_action,
        "confidence": decision.confidence,
        "reason": decision.safety_reason,
        "safety_checked": True,
        "proposed_at": decision.ts.isoformat(),
        "decision": decision.model_dump(mode="json"),
    }


def _observation_payload(junction_id: str) -> dict[str, Any]:
    observation = signal_controller.state(junction_id)
    latest = signal_controller.latest_decision(junction_id)
    return {
        "junction_id": observation.junction_id,
        "current_phase": observation.current_phase,
        "phase_index": observation.phase_index,
        "phase_elapsed_s": round(observation.phase_elapsed_s, 3),
        "phase_duration_s": round(observation.phase_elapsed_s, 3),
        "phase_remaining_s": round(observation.phase_remaining_s, 3),
        "total_queue": observation.total_queue(),
        "total_ped_demand": observation.total_ped_demand(),
        "avg_speed_mps": round(observation.avg_speed(), 3),
        "approaches": [
            {
                "approach_id": movement,
                "queue_length": item.queue_length,
                "lane_density": round(item.density, 4),
                "avg_speed_mps": round(item.avg_speed_mps, 3),
                "incoming_flow": round(item.incoming_flow, 3),
                "downstream_occupancy": round(item.downstream_occupancy, 4),
                "pedestrian_demand": item.pedestrian_demand,
                "vru_density": round(item.vru_density, 4),
            }
            for movement, item in observation.approaches.items()
        ],
        "last_recommendation": _recommendation_payload(latest) if latest else None,
    }


@router.post("/topologies", status_code=201)
async def register_topology(topology: SignalJunctionTopology) -> dict[str, Any]:
    return cast(dict[str, Any], signal_controller.register_topology(topology).model_dump(mode="json"))


@router.get("/topologies")
async def list_topologies() -> dict[str, Any]:
    """Report which junctions the controller can actually act on."""
    return {"junction_ids": list(signal_controller.junction_ids)}


@router.get("/commands/history")
async def command_history() -> dict[str, Any]:
    """Read issued timing changes without consuming simulator commands."""
    return {"commands": list(_command_history)}


@router.get("/commands/pending")
async def drain_pending_commands() -> dict[str, Any]:
    """Drain applied signal commands for a simulator that polls the gateway.

    Destructive by design: each command is delivered to exactly one caller so a
    single simulator applies each RL action once.
    """
    commands = list(_pending_commands)
    _pending_commands.clear()
    return {"commands": commands, "count": len(commands)}


@router.get("/{junction_id}/state")
async def get_state(junction_id: str) -> dict[str, Any]:
    try:
        return _observation_payload(junction_id)
    except SignalControlError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{junction_id}/recommend")
async def recommend(junction_id: str, request: RecommendRequest | None = None) -> dict[str, Any]:
    try:
        decision = signal_controller.recommend(junction_id)
        if request and request.apply:
            decision = signal_controller.apply(decision.decision_id)
        return _recommendation_payload(decision)
    except SignalControlError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{junction_id}/apply")
async def apply(junction_id: str, request: ApplyRequest) -> dict[str, Any]:
    try:
        if request.decision_id:
            decision = signal_controller.apply(request.decision_id)
        elif request.action:
            try:
                decision = signal_controller.recommend(junction_id, SignalAction(request.action))
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=f"unknown signal action {request.action!r}") from exc
            decision = signal_controller.apply(decision.decision_id)
        else:
            decision = signal_controller.decide_and_apply(junction_id)
    except SignalControlError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if decision.junction_id != junction_id:
        raise HTTPException(status_code=409, detail="decision belongs to a different junction")
    return {
        "junction_id": decision.junction_id,
        "applied_action": decision.effective_action,
        "applied_at": decision.ts.isoformat(),
        "decision": decision.model_dump(mode="json"),
    }


@router.get("/decisions/{decision_id}")
async def get_decision(decision_id: str) -> dict[str, Any]:
    decision = signal_controller.get_decision(decision_id)
    if decision is None:
        raise HTTPException(status_code=404, detail=f"unknown signal decision {decision_id!r}")
    return cast(dict[str, Any], decision.model_dump(mode="json"))
