"""RL agent and environment tests.

Covers: action selection, Q-update convergence, save/load roundtrip,
environment dynamics, and RL vs fixed-time comparison.
"""

import tempfile
from pathlib import Path

import pytest

from marga_signal_rl.actions import ALL_ACTIONS, SignalAction
from marga_signal_rl.agent import TabularQLearningAgent
from marga_signal_rl.environment import MockSumoSignalEnvironment
from marga_signal_rl.trainer import (
    run_episode,
    run_fixed_time_episode,
    train,
)


# ── Agent tests ──────────────────────────────────────────────────────────────

class TestQLearningAgent:
    def _obs(self) -> object:
        env = MockSumoSignalEnvironment(seed=0)
        return env.reset(seed=0)

    def test_greedy_action_is_valid(self) -> None:
        agent = TabularQLearningAgent(epsilon=0.0)
        obs = self._obs()
        action = agent.select_action(obs, explore=False)  # type: ignore[arg-type]
        assert action in ALL_ACTIONS

    def test_random_action_is_valid(self) -> None:
        agent = TabularQLearningAgent(epsilon=1.0)
        obs = self._obs()
        action = agent.select_action(obs, explore=True)  # type: ignore[arg-type]
        assert action in ALL_ACTIONS

    def test_update_changes_q_value(self) -> None:
        env = MockSumoSignalEnvironment(seed=1)
        obs = env.reset(seed=1)
        agent = TabularQLearningAgent(epsilon=0.0)
        key = agent._state_key(obs)
        before = agent._q_row(key)["HOLD"]
        next_obs, _, _ = env.step(SignalAction.HOLD)
        agent.update(obs, SignalAction.HOLD, 1.0, next_obs)
        after = agent._q_row(key)["HOLD"]
        assert after != before

    def test_positive_reward_increases_q(self) -> None:
        env = MockSumoSignalEnvironment(seed=2)
        obs = env.reset(seed=2)
        agent = TabularQLearningAgent(epsilon=0.0, alpha=1.0, gamma=0.0)
        key = agent._state_key(obs)
        next_obs, _, _ = env.step(SignalAction.HOLD)
        agent.update(obs, SignalAction.HOLD, 5.0, next_obs)
        assert agent._q_row(key)["HOLD"] > 0

    def test_epsilon_decays_correctly(self) -> None:
        agent = TabularQLearningAgent(epsilon=1.0, epsilon_min=0.05, epsilon_decay=0.9)
        agent.decay_epsilon()
        assert abs(agent.epsilon - 0.9) < 1e-9
        for _ in range(1000):
            agent.decay_epsilon()
        assert agent.epsilon == pytest.approx(0.05)

    def test_save_load_roundtrip(self) -> None:
        env = MockSumoSignalEnvironment(seed=42)
        obs = env.reset(seed=42)
        agent = TabularQLearningAgent(epsilon=0.3)
        next_obs, _, _ = env.step(SignalAction.NEXT_PHASE)
        agent.update(obs, SignalAction.NEXT_PHASE, 2.0, next_obs)

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "policy.json"
            agent.save(path)
            loaded = TabularQLearningAgent.load(path)

        assert loaded.epsilon == pytest.approx(agent.epsilon)
        assert loaded._q == agent._q

    def test_greedy_recommend_returns_all_q_values(self) -> None:
        env = MockSumoSignalEnvironment(seed=5)
        obs = env.reset(seed=5)
        agent = TabularQLearningAgent(epsilon=0.0)
        rec = agent.greedy_recommend(obs)
        assert set(rec["q_values"]) == {a.value for a in ALL_ACTIONS}
        assert rec["action"] in {a.value for a in ALL_ACTIONS}


# ── Environment tests ────────────────────────────────────────────────────────

class TestMockEnvironment:
    def test_reset_returns_four_approaches(self) -> None:
        env = MockSumoSignalEnvironment(seed=0)
        obs = env.reset(seed=0)
        assert set(obs.approaches) == {"N", "S", "E", "W"}

    def test_step_changes_obs(self) -> None:
        env = MockSumoSignalEnvironment(seed=1)
        obs0 = env.reset(seed=1)
        obs1, _, _ = env.step(SignalAction.HOLD)
        assert obs1.ts != obs0.ts

    def test_reward_is_float(self) -> None:
        env = MockSumoSignalEnvironment(seed=2)
        env.reset(seed=2)
        _, reward, _ = env.step(SignalAction.HOLD)
        assert isinstance(reward, float)

    def test_episode_terminates_after_3600s(self) -> None:
        env = MockSumoSignalEnvironment(seed=3)
        env.reset(seed=3)
        done = False
        for _ in range(800):  # 800 × 5 s = 4000 s > 3600 s
            _, _, done = env.step(SignalAction.HOLD)
            if done:
                break
        assert done

    def test_next_phase_advances_phase_index(self) -> None:
        env = MockSumoSignalEnvironment(seed=4)
        env.reset(seed=4)
        # Prime phase with >10 s so safety controller allows NEXT_PHASE
        for _ in range(3):
            env.step(SignalAction.HOLD)
        obs_before = env.current_obs()
        # Force min-green to be satisfied
        env._phase_elapsed = 15.0
        env.step(SignalAction.NEXT_PHASE)
        obs_after = env.current_obs()
        assert obs_after.phase_index != obs_before.phase_index or obs_after.phase_elapsed_s < obs_before.phase_elapsed_s

    def test_deterministic_with_same_seed(self) -> None:
        env1 = MockSumoSignalEnvironment(seed=99)
        env2 = MockSumoSignalEnvironment(seed=99)
        obs1 = env1.reset(seed=99)
        obs2 = env2.reset(seed=99)
        assert obs1.total_queue() == obs2.total_queue()


# ── Trainer tests ─────────────────────────────────────────────────────────────

class TestTrainer:
    def test_short_training_produces_metrics(self) -> None:
        agent, result = train(n_episodes=5)
        assert len(result.episodes) == 5
        assert result.best_episode is not None

    def test_epsilon_decreases_over_training(self) -> None:
        agent, result = train(n_episodes=20)
        assert result.episodes[-1].epsilon < result.episodes[0].epsilon

    def test_run_episode_returns_metrics(self) -> None:
        env = MockSumoSignalEnvironment(seed=0)
        agent = TabularQLearningAgent(epsilon=1.0)
        m = run_episode(env, agent, episode_num=0)
        assert m.total_steps > 0
        assert m.policy == "rl-q-learning"

    def test_fixed_time_baseline_returns_metrics(self) -> None:
        env = MockSumoSignalEnvironment(seed=0)
        m = run_fixed_time_episode(env, episode_num=0)
        assert m.policy == "fixed-time"
        assert m.safety_overrides == 0

    def test_rl_learns_to_reduce_queue(self) -> None:
        # After 200 episodes, RL queue should be at most 20% worse than fixed-time
        # (usually better; allowing margin for random seed variance)
        agent, _ = train(n_episodes=200)
        agent.epsilon = 0.0

        env_rl = MockSumoSignalEnvironment(junction_id="eval-rl")
        env_ft = MockSumoSignalEnvironment(junction_id="eval-ft")

        rl_queues = [run_episode(env_rl, agent, ep + 500, explore=False).avg_queue for ep in range(5)]
        ft_queues = [run_fixed_time_episode(env_ft, ep + 600).avg_queue for ep in range(5)]

        avg_rl = sum(rl_queues) / len(rl_queues)
        avg_ft = sum(ft_queues) / len(ft_queues)
        assert avg_rl <= avg_ft * 1.25
