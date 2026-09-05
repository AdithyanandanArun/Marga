"""Train the signal RL policy and save it.

Run with:  python -m marga_signal_rl [--episodes N]
"""

from __future__ import annotations

import argparse
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

from .trainer import POLICY_PATH, compare_policies, train, train_on_sumo


def main() -> None:
    parser = argparse.ArgumentParser(description="Train Marga signal RL policy")
    parser.add_argument("--episodes", type=int, default=300, help="training episodes (default 300)")
    parser.add_argument("--compare", action="store_true", help="run policy comparison after training")
    parser.add_argument("--sumo-host", help="dedicated TraCI SUMO host for live-environment training")
    parser.add_argument("--sumo-port", type=int, default=8813, help="TraCI port (default 8813)")
    parser.add_argument("--junction-id", help="traffic-light ID when training against SUMO")
    args = parser.parse_args()

    if args.sumo_host:
        if not args.junction_id:
            parser.error("--junction-id is required with --sumo-host")
        agent, result = train_on_sumo(
            args.junction_id, host=args.sumo_host, port=args.sumo_port, n_episodes=args.episodes
        )
    else:
        agent, result = train(args.episodes)
    agent.save(POLICY_PATH)
    best = result.best_episode
    print(f"\nTraining complete — {args.episodes} episodes")
    print(f"  Q-table states : {len(agent._q)}")
    print(f"  Policy saved   : {POLICY_PATH}")
    if best:
        print(f"  Best episode   : #{best.episode}  reward={best.total_reward}  queue={best.avg_queue}  speed={best.avg_speed}")

    if args.compare:
        cmp = compare_policies(n_eval_episodes=10)
        rl = cmp["rl_q_learning"]
        ft = cmp["fixed_time"]
        print("\nPolicy comparison (10 eval episodes):")
        print(f"  {'Metric':<25} {'RL':>10} {'Fixed-time':>12}")
        print(f"  {'avg_queue':<25} {rl['avg_queue']:>10.2f} {ft['avg_queue']:>12.2f}")
        print(f"  {'avg_speed_mps':<25} {rl['avg_speed_mps']:>10.3f} {ft['avg_speed_mps']:>12.3f}")
        print(f"  {'avg_episode_reward':<25} {rl['avg_episode_reward']:>10.3f} {ft['avg_episode_reward']:>12.3f}")


if __name__ == "__main__":
    main()
