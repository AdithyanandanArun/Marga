"""FastAPI application exposing the signal RL endpoints.

POST /signals/{junction_id}/recommend  — query the RL agent for an action
POST /signals/{junction_id}/apply      — apply an action (mock env or SUMO)
GET  /signals/{junction_id}/state      — current signal + queue state
POST /signals/train                    — train the agent and persist policy
GET  /signals/compare                  — compare RL vs fixed-time baseline
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from .actions import SignalAction
from .agent import TabularQLearningAgent
from .environment import MockSumoSignalEnvironment
from .safety import SignalSafetyController
from .state import ApproachState, SignalObservation
from .trainer import POLICY_PATH, compare_policies, train

log = logging.getLogger(__name__)

_agent: Optional[TabularQLearningAgent] = None
_envs: dict[str, MockSumoSignalEnvironment] = {}
_safety = SignalSafetyController()


@asynccontextmanager
async def lifespan(app: FastAPI):  # type: ignore[type-arg]
    global _agent
    if POLICY_PATH.exists():
        _agent = TabularQLearningAgent.load(POLICY_PATH)
        _agent.epsilon = 0.0
        log.info("Loaded signal policy (%d states) from %s", len(_agent._q), POLICY_PATH)
    else:
        log.warning("No trained policy at %s — training 300 episodes on startup", POLICY_PATH)
        _agent, _ = train(300)
        _agent.save(POLICY_PATH)
        _agent.epsilon = 0.0
    yield


app = FastAPI(
    title="Marga Signal RL",
    description="RL-based adaptive signal controller with deterministic safety envelope",
    version="1.0.0",
    lifespan=lifespan,
)


def _env(junction_id: str) -> MockSumoSignalEnvironment:
    if junction_id not in _envs:
        e = MockSumoSignalEnvironment(junction_id=junction_id)
        e.reset()
        _envs[junction_id] = e
    return _envs[junction_id]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Request / response models ────────────────────────────────────────────────

class ApproachInput(BaseModel):
    queue_length: int
    density: float
    avg_speed_mps: float
    incoming_flow: float
    downstream_occupancy: float
    pedestrian_demand: int
    vru_density: float


class RecommendRequest(BaseModel):
    """Graph-derived junction snapshot from the mobility graph (Adithyan1)."""

    current_phase: str
    phase_index: int
    phase_elapsed_s: float
    phase_remaining_s: float
    approaches: dict[str, ApproachInput]


class RecommendResponse(BaseModel):
    junction_id: str
    recommended_action: str
    effective_action: str
    safety_override: bool
    safety_reason: str
    q_values: dict[str, float]
    model: str
    ts: str


class ApplyRequest(BaseModel):
    action: str
    sumo_host: Optional[str] = None
    sumo_port: Optional[int] = None


class ApplyResponse(BaseModel):
    junction_id: str
    action_requested: str
    action_applied: str
    safety_override: bool
    sumo_applied: bool
    new_phase: str
    new_phase_elapsed_s: float
    new_total_queue: int
    ts: str


class SignalStateResponse(BaseModel):
    junction_id: str
    current_phase: str
    phase_index: int
    phase_elapsed_s: float
    phase_remaining_s: float
    total_queue: int
    total_ped_demand: int
    avg_speed_mps: float
    approaches: dict
    ts: str


# ── Endpoints ────────────────────────────────────────────────────────────────

@app.get("/signals/{junction_id}/state", response_model=SignalStateResponse)
def get_state(junction_id: str) -> SignalStateResponse:
    obs = _env(junction_id).current_obs()
    return SignalStateResponse(
        junction_id=junction_id,
        current_phase=obs.current_phase,
        phase_index=obs.phase_index,
        phase_elapsed_s=obs.phase_elapsed_s,
        phase_remaining_s=obs.phase_remaining_s,
        total_queue=obs.total_queue(),
        total_ped_demand=obs.total_ped_demand(),
        avg_speed_mps=round(obs.avg_speed(), 2),
        approaches={
            k: {
                "queue_length": v.queue_length,
                "density": round(v.density, 3),
                "avg_speed_mps": round(v.avg_speed_mps, 2),
                "incoming_flow": round(v.incoming_flow, 2),
                "downstream_occupancy": round(v.downstream_occupancy, 3),
                "pedestrian_demand": v.pedestrian_demand,
            }
            for k, v in obs.approaches.items()
        },
        ts=_now(),
    )


@app.post("/signals/{junction_id}/recommend", response_model=RecommendResponse)
def recommend(junction_id: str, body: RecommendRequest) -> RecommendResponse:
    if _agent is None:
        raise HTTPException(503, "Agent not initialised")

    approaches = {
        k: ApproachState(
            movement_id=k,
            queue_length=v.queue_length,
            density=v.density,
            avg_speed_mps=v.avg_speed_mps,
            incoming_flow=v.incoming_flow,
            downstream_occupancy=v.downstream_occupancy,
            pedestrian_demand=v.pedestrian_demand,
            vru_density=v.vru_density,
        )
        for k, v in body.approaches.items()
    }
    obs = SignalObservation(
        junction_id=junction_id,
        current_phase=body.current_phase,
        phase_index=body.phase_index,
        phase_elapsed_s=body.phase_elapsed_s,
        phase_remaining_s=body.phase_remaining_s,
        approaches=approaches,
        ts=_now(),
    )

    rec = _agent.greedy_recommend(obs)
    proposed = SignalAction(rec["action"])
    verdict = _safety.validate(obs, proposed)

    return RecommendResponse(
        junction_id=junction_id,
        recommended_action=proposed.value,
        effective_action=verdict.action.value,
        safety_override=verdict.safety_override,
        safety_reason=verdict.reason,
        q_values=rec["q_values"],
        model=rec["model"],
        ts=_now(),
    )


@app.post("/signals/{junction_id}/apply", response_model=ApplyResponse)
def apply_action(junction_id: str, body: ApplyRequest) -> ApplyResponse:
    try:
        action = SignalAction(body.action)
    except ValueError:
        raise HTTPException(400, f"Unknown action {body.action!r}. Valid: {[a.value for a in SignalAction]}")

    env = _env(junction_id)
    obs_before = env.current_obs()
    verdict = _safety.validate(obs_before, action)

    obs_after, _, _ = env.step(verdict.action)

    sumo_applied = False
    if body.sumo_host:
        sumo_applied = _push_to_sumo(body.sumo_host, body.sumo_port or 8813, junction_id, verdict.action, obs_after)

    return ApplyResponse(
        junction_id=junction_id,
        action_requested=action.value,
        action_applied=verdict.action.value,
        safety_override=verdict.safety_override,
        sumo_applied=sumo_applied,
        new_phase=obs_after.current_phase,
        new_phase_elapsed_s=obs_after.phase_elapsed_s,
        new_total_queue=obs_after.total_queue(),
        ts=_now(),
    )


@app.post("/signals/train")
def train_agent(n_episodes: int = 300) -> dict:
    """Train the agent and persist the policy. Returns training summary."""
    global _agent
    agent, result = train(n_episodes)
    agent.save(POLICY_PATH)
    agent.epsilon = 0.0
    _agent = agent
    best = result.best_episode
    return {
        "trained_episodes": n_episodes,
        "q_table_states": len(agent._q),
        "policy_path": str(POLICY_PATH),
        "best_episode": {
            "episode": best.episode,
            "total_reward": best.total_reward,
            "avg_queue": best.avg_queue,
            "avg_speed": best.avg_speed,
        } if best else None,
    }


@app.get("/signals/compare")
def policy_comparison(n_eval_episodes: int = 10) -> dict:
    """Compare the trained RL policy against a fixed-time baseline."""
    return compare_policies(n_eval_episodes)


# ── SUMO integration helper ──────────────────────────────────────────────────

def _push_to_sumo(host: str, port: int, junction_id: str, action: SignalAction, obs: SignalObservation) -> bool:
    try:
        import traci  # type: ignore[import]
    except ImportError:
        log.warning("traci not installed — SUMO push skipped")
        return False
    try:
        conn = traci.connect(host=host, port=port)
        if action is SignalAction.NEXT_PHASE:
            conn.trafficlight.setPhase(junction_id, (obs.phase_index + 1) % 4)
        elif action is SignalAction.EXTEND_GREEN_5:
            conn.trafficlight.setPhaseDuration(junction_id, obs.phase_remaining_s + 5.0)
        elif action is SignalAction.EXTEND_GREEN_10:
            conn.trafficlight.setPhaseDuration(junction_id, obs.phase_remaining_s + 10.0)
        conn.close()
        return True
    except Exception as exc:
        log.warning("SUMO push failed for %s: %s", junction_id, exc)
        return False
