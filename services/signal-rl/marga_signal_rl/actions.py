"""Signal control actions for the RL agent."""

from __future__ import annotations

from enum import Enum


class SignalAction(str, Enum):
    HOLD = "HOLD"
    EXTEND_GREEN_5 = "EXTEND_GREEN_5"
    EXTEND_GREEN_10 = "EXTEND_GREEN_10"
    NEXT_PHASE = "NEXT_PHASE"


# How much extra green time (seconds) each action adds to the current phase.
# NEXT_PHASE resets to the next program step; its delta is applied by the phase program.
ACTION_EXTENSION_S: dict[str, float] = {
    SignalAction.HOLD: 0.0,
    SignalAction.EXTEND_GREEN_5: 5.0,
    SignalAction.EXTEND_GREEN_10: 10.0,
    SignalAction.NEXT_PHASE: 0.0,
}

ALL_ACTIONS: list[SignalAction] = list(SignalAction)
