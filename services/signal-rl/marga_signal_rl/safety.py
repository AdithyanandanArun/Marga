"""Signal safety controller — every RL proposal passes through here before execution.

Safety rules are deterministic and non-negotiable. If the RL agent's proposal
violates a rule the controller either overrides the action or substitutes a safe
alternative. The override is always logged so it can be replayed for audit.
"""

from __future__ import annotations

from dataclasses import dataclass

from .actions import SignalAction
from .state import SignalObservation

# Safety thresholds (seconds)
MIN_GREEN_S: float = 10.0    # minimum green before NEXT_PHASE is permitted
MAX_GREEN_S: float = 90.0    # maximum green before NEXT_PHASE is forced
MIN_YELLOW_S: float = 3.0    # yellow clearance (enforced by the phase programme itself)
ALL_RED_GAP_S: float = 1.5   # all-red inter-green (enforced by the phase programme itself)
# Pedestrian clearance: if phase_remaining < this threshold AND pedestrians are waiting, hold
PED_CLEARANCE_BUFFER_S: float = 5.0
# Extension cap: don't extend if phase is already this close to MAX_GREEN_S
EXTENSION_HEADROOM_S: float = 15.0


@dataclass(frozen=True)
class SafetyVerdict:
    allowed: bool                  # always True — controller substitutes rather than blocks
    action: SignalAction           # effective action (may differ from proposal)
    reason: str
    safety_override: bool          # True when controller changed the proposed action


class SignalSafetyController:
    """
    Deterministic safety envelope applied before every RL action reaches SUMO.

    Rule evaluation order is significant: later rules assume earlier ones passed.
    """

    def validate(self, obs: SignalObservation, proposed: SignalAction) -> SafetyVerdict:
        elapsed = obs.phase_elapsed_s
        remaining = obs.phase_remaining_s

        # Rule 1 — minimum green: NEXT_PHASE is not allowed before MIN_GREEN_S
        if proposed is SignalAction.NEXT_PHASE and elapsed < MIN_GREEN_S:
            return SafetyVerdict(
                allowed=True,
                action=SignalAction.HOLD,
                reason=f"min-green not satisfied ({elapsed:.1f}s < {MIN_GREEN_S}s); holding",
                safety_override=True,
            )

        # Rule 2 — maximum green: force NEXT_PHASE if phase has run too long
        if elapsed >= MAX_GREEN_S and proposed is not SignalAction.NEXT_PHASE:
            return SafetyVerdict(
                allowed=True,
                action=SignalAction.NEXT_PHASE,
                reason=f"max-green exceeded ({elapsed:.1f}s ≥ {MAX_GREEN_S}s); forcing NEXT_PHASE",
                safety_override=True,
            )

        # Rule 3 — pedestrian clearance: cannot transition while pedestrians need to clear
        if proposed is SignalAction.NEXT_PHASE and obs.total_ped_demand() > 0:
            if remaining < PED_CLEARANCE_BUFFER_S:
                return SafetyVerdict(
                    allowed=True,
                    action=SignalAction.HOLD,
                    reason=(
                        f"pedestrian clearance: {obs.total_ped_demand()} pedestrians waiting, "
                        f"only {remaining:.1f}s remaining (< {PED_CLEARANCE_BUFFER_S}s buffer)"
                    ),
                    safety_override=True,
                )

        # Rule 4 — extension cap: block extension if already near max green
        if proposed in (SignalAction.EXTEND_GREEN_5, SignalAction.EXTEND_GREEN_10):
            if elapsed > MAX_GREEN_S - EXTENSION_HEADROOM_S:
                return SafetyVerdict(
                    allowed=True,
                    action=SignalAction.HOLD,
                    reason=(
                        f"extension blocked: phase already at {elapsed:.1f}s "
                        f"(headroom limit {MAX_GREEN_S - EXTENSION_HEADROOM_S:.0f}s)"
                    ),
                    safety_override=True,
                )

        return SafetyVerdict(allowed=True, action=proposed, reason="ok", safety_override=False)
