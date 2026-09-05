"""Reward function for the signal RL agent.

Reward is computed from the delta between two consecutive observations.
Positive signal: throughput improvement, speed improvement.
Negative signal: queue growth, spillback, pedestrian wait, stops.
"""

from __future__ import annotations

from .state import SignalObservation

# Reward weights — tuned to balance queue reduction vs throughput vs safety
QUEUE_WEIGHT: float = -0.40       # penalise queue growth
SPEED_WEIGHT: float = 0.25        # reward speed recovery
FLOW_WEIGHT: float = 0.15         # reward throughput increase
PED_WAIT_WEIGHT: float = -0.20    # penalise stranded pedestrians


def compute_reward(before: SignalObservation, after: SignalObservation) -> float:
    delta_queue = after.total_queue() - before.total_queue()
    delta_speed = after.avg_speed() - before.avg_speed()
    delta_flow = after.total_flow() - before.total_flow()
    ped_demand = after.total_ped_demand()

    return (
        QUEUE_WEIGHT * delta_queue
        + SPEED_WEIGHT * delta_speed
        + FLOW_WEIGHT * delta_flow
        + PED_WAIT_WEIGHT * ped_demand
    )
