"""Tabular Q-learning agent for signal control.

State space is discretized into a compact key so the Q-table is small enough
to inspect, save as JSON, and load without any ML framework dependency.

Exploration uses ε-greedy with exponential decay. At inference time ε is set
to zero so the greedy policy is used deterministically.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

from .actions import ALL_ACTIONS, SignalAction
from .state import SignalObservation

POLICY_VERSION = "tabular-q-learning-v1"


def _bucket_queue(q: int) -> int:
    """Discretize queue length into 4 bins."""
    if q <= 2:
        return 0
    if q <= 6:
        return 1
    if q <= 12:
        return 2
    return 3


def _bucket_elapsed(s: float) -> int:
    """Discretize phase elapsed time into 4 bins."""
    if s < 10:
        return 0
    if s < 20:
        return 1
    if s < 40:
        return 2
    return 3


def _obs_to_key(obs: SignalObservation) -> tuple[int, int, int, int, int]:
    """Map an observation to a compact discrete state key."""
    max_q_dir = obs.max_queue_approach()
    max_q = obs.approaches[max_q_dir].queue_length if max_q_dir else 0
    return (
        obs.phase_index % 4,
        _bucket_queue(obs.total_queue()),
        _bucket_queue(max_q),
        _bucket_elapsed(obs.phase_elapsed_s),
        min(obs.total_ped_demand(), 3),
    )


class TabularQLearningAgent:
    """
    ε-greedy tabular Q-learning agent.

    Q-table is stored as a plain dict keyed by stringified state tuples so it
    serialises to JSON without any special encoding.
    """

    def __init__(
        self,
        alpha: float = 0.1,
        gamma: float = 0.95,
        epsilon: float = 1.0,
        epsilon_min: float = 0.05,
        epsilon_decay: float = 0.995,
    ) -> None:
        self.alpha = alpha
        self.gamma = gamma
        self.epsilon = epsilon
        self.epsilon_min = epsilon_min
        self.epsilon_decay = epsilon_decay
        self._q: dict[str, dict[str, float]] = {}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _state_key(self, obs: SignalObservation) -> str:
        return str(_obs_to_key(obs))

    def _q_row(self, key: str) -> dict[str, float]:
        if key not in self._q:
            self._q[key] = {a.value: 0.0 for a in ALL_ACTIONS}
        return self._q[key]

    # ------------------------------------------------------------------
    # Policy
    # ------------------------------------------------------------------

    def select_action(self, obs: SignalObservation, *, explore: bool = True) -> SignalAction:
        if explore and random.random() < self.epsilon:
            return random.choice(ALL_ACTIONS)
        row = self._q_row(self._state_key(obs))
        return SignalAction(max(row, key=row.__getitem__))

    def greedy_recommend(self, obs: SignalObservation) -> dict:
        """Return the greedy action with full Q-values for API responses."""
        key = self._state_key(obs)
        row = self._q_row(key)
        best = max(row, key=row.__getitem__)
        return {
            "action": best,
            "q_values": {k: round(v, 4) for k, v in row.items()},
            "state_key": key,
            "model": POLICY_VERSION,
        }

    # ------------------------------------------------------------------
    # Learning
    # ------------------------------------------------------------------

    def update(
        self,
        obs: SignalObservation,
        action: SignalAction,
        reward: float,
        next_obs: SignalObservation,
    ) -> None:
        key = self._state_key(obs)
        next_key = self._state_key(next_obs)
        row = self._q_row(key)
        next_row = self._q_row(next_key)

        best_next = max(next_row.values())
        td_target = reward + self.gamma * best_next
        row[action.value] += self.alpha * (td_target - row[action.value])

    def decay_epsilon(self) -> None:
        self.epsilon = max(self.epsilon_min, self.epsilon * self.epsilon_decay)

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save(self, path: Path) -> None:
        path.write_text(
            json.dumps(
                {
                    "version": POLICY_VERSION,
                    "alpha": self.alpha,
                    "gamma": self.gamma,
                    "epsilon": round(self.epsilon, 6),
                    "epsilon_min": self.epsilon_min,
                    "epsilon_decay": self.epsilon_decay,
                    "q_table": self._q,
                },
                indent=2,
            )
        )

    @classmethod
    def load(cls, path: Path) -> TabularQLearningAgent:
        data = json.loads(path.read_text())
        agent = cls(
            alpha=data.get("alpha", 0.1),
            gamma=data.get("gamma", 0.95),
            epsilon=data.get("epsilon", 0.05),
            epsilon_min=data.get("epsilon_min", 0.05),
            epsilon_decay=data.get("epsilon_decay", 0.995),
        )
        agent._q = data.get("q_table", {})
        return agent
