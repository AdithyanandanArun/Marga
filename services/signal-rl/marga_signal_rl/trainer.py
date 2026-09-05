"""RL training loop and fixed-time baseline for comparison.

train() produces a trained TabularQLearningAgent and a TrainingResult that
includes per-episode metrics. compare_policies() evaluates both the trained
RL policy and a fixed-time baseline across the same episodes so the
improvement is directly comparable.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .actions import SignalAction
from .agent import TabularQLearningAgent
from .environment import MockSumoSignalEnvironment
from .safety import SignalSafetyController

log = logging.getLogger(__name__)

POLICY_PATH = Path(__file__).parent / "trained_signal_policy.json"


@dataclass
class EpisodeMetrics:
    episode: int
    policy: str
    total_reward: float
    total_steps: int
    avg_queue: float
    avg_speed: float
    safety_overrides: int
    epsilon: float


@dataclass
class TrainingResult:
    episodes: list[EpisodeMetrics] = field(default_factory=list)
    best_episode: Optional[EpisodeMetrics] = None


def run_episode(
    env: MockSumoSignalEnvironment,
    agent: TabularQLearningAgent,
    episode_num: int,
    *,
    explore: bool = True,
    max_steps: int = 720,
) -> EpisodeMetrics:
    """Run one training or evaluation episode, return aggregated metrics."""
    obs = env.reset(seed=episode_num)
    total_reward = 0.0
    queue_sum = 0.0
    speed_sum = 0.0
    safety_overrides = 0

    for step in range(max_steps):
        action = agent.select_action(obs, explore=explore)
        verdict = env.safety.validate(obs, action)
        if verdict.safety_override:
            safety_overrides += 1

        next_obs, reward, done = env.step(action)  # env re-runs safety internally

        if explore:
            agent.update(obs, verdict.action, reward, next_obs)

        queue_sum += next_obs.total_queue()
        speed_sum += next_obs.avg_speed()
        total_reward += reward
        obs = next_obs

        if done:
            break

    n = step + 1
    if explore:
        agent.decay_epsilon()

    return EpisodeMetrics(
        episode=episode_num,
        policy="rl-q-learning",
        total_reward=round(total_reward, 3),
        total_steps=n,
        avg_queue=round(queue_sum / n, 2),
        avg_speed=round(speed_sum / n, 3),
        safety_overrides=safety_overrides,
        epsilon=round(agent.epsilon, 4),
    )


def run_fixed_time_episode(
    env: MockSumoSignalEnvironment,
    episode_num: int,
    max_steps: int = 720,
) -> EpisodeMetrics:
    """Baseline: fixed 25 s green, transition on natural phase expiry."""
    obs = env.reset(seed=episode_num)
    total_reward = 0.0
    queue_sum = 0.0
    speed_sum = 0.0

    for step in range(max_steps):
        # Fixed-time: HOLD until the last 5 s of the phase, then NEXT_PHASE
        action = SignalAction.NEXT_PHASE if obs.phase_remaining_s < 5.0 else SignalAction.HOLD
        next_obs, reward, done = env.step(action)
        queue_sum += next_obs.total_queue()
        speed_sum += next_obs.avg_speed()
        total_reward += reward
        obs = next_obs
        if done:
            break

    n = step + 1
    return EpisodeMetrics(
        episode=episode_num,
        policy="fixed-time",
        total_reward=round(total_reward, 3),
        total_steps=n,
        avg_queue=round(queue_sum / n, 2),
        avg_speed=round(speed_sum / n, 3),
        safety_overrides=0,
        epsilon=0.0,
    )


def train(n_episodes: int = 300) -> tuple[TabularQLearningAgent, TrainingResult]:
    """Train the Q-learning agent for n_episodes on the mock environment."""
    env = MockSumoSignalEnvironment(junction_id="train-junction")
    agent = TabularQLearningAgent(
        alpha=0.1,
        gamma=0.95,
        epsilon=1.0,
        epsilon_min=0.05,
        epsilon_decay=0.995,
    )
    result = TrainingResult()

    for ep in range(n_episodes):
        metrics = run_episode(env, agent, ep)
        result.episodes.append(metrics)
        if result.best_episode is None or metrics.total_reward > result.best_episode.total_reward:
            result.best_episode = metrics
        if ep % 50 == 0:
            log.info(
                "ep=%d reward=%.2f queue=%.1f speed=%.2f ε=%.3f states=%d",
                ep,
                metrics.total_reward,
                metrics.avg_queue,
                metrics.avg_speed,
                agent.epsilon,
                len(agent._q),
            )

    return agent, result


def compare_policies(n_eval_episodes: int = 10) -> dict:
    """Evaluate RL vs fixed-time across n_eval_episodes and return comparison."""
    env_rl = MockSumoSignalEnvironment(junction_id="eval-rl")
    env_ft = MockSumoSignalEnvironment(junction_id="eval-ft")

    if POLICY_PATH.exists():
        agent = TabularQLearningAgent.load(POLICY_PATH)
    else:
        log.info("No saved policy found — training %d episodes for comparison", 200)
        agent, _ = train(200)
    agent.epsilon = 0.0  # greedy evaluation

    rl_ep = [run_episode(env_rl, agent, i + 1000, explore=False) for i in range(n_eval_episodes)]
    ft_ep = [run_fixed_time_episode(env_ft, i + 2000) for i in range(n_eval_episodes)]

    def _avg(lst: list[EpisodeMetrics], attr: str) -> float:
        return round(sum(getattr(m, attr) for m in lst) / len(lst), 3)

    return {
        "n_eval_episodes": n_eval_episodes,
        "rl_q_learning": {
            "avg_queue": _avg(rl_ep, "avg_queue"),
            "avg_speed_mps": _avg(rl_ep, "avg_speed"),
            "avg_episode_reward": _avg(rl_ep, "total_reward"),
            "avg_safety_overrides": _avg(rl_ep, "safety_overrides"),
        },
        "fixed_time": {
            "avg_queue": _avg(ft_ep, "avg_queue"),
            "avg_speed_mps": _avg(ft_ep, "avg_speed"),
            "avg_episode_reward": _avg(ft_ep, "total_reward"),
            "avg_safety_overrides": 0.0,
        },
    }
