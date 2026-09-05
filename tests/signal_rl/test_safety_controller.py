"""Safety controller contract tests.

Every RL proposal must pass through the safety controller before reaching SUMO.
These tests assert the deterministic rules that can never be violated.
"""

import pytest

from marga_signal_rl.actions import SignalAction
from marga_signal_rl.safety import (
    MAX_GREEN_S,
    MIN_GREEN_S,
    PED_CLEARANCE_BUFFER_S,
    SignalSafetyController,
)
from marga_signal_rl.state import ApproachState, SignalObservation


def _obs(
    *,
    phase_elapsed: float,
    phase_remaining: float,
    ped: int = 0,
    phase_index: int = 0,
) -> SignalObservation:
    approach = ApproachState(
        movement_id="N",
        queue_length=5,
        density=10.0,
        avg_speed_mps=3.0,
        incoming_flow=4.0,
        downstream_occupancy=0.3,
        pedestrian_demand=ped,
        vru_density=0.0,
    )
    return SignalObservation(
        junction_id="test",
        current_phase="NS_GREEN",
        phase_index=phase_index,
        phase_elapsed_s=phase_elapsed,
        phase_remaining_s=phase_remaining,
        approaches={"N": approach},
        ts="now",
    )


@pytest.fixture
def ctrl() -> SignalSafetyController:
    return SignalSafetyController()


class TestMinGreen:
    def test_next_phase_before_min_green_is_overridden(self, ctrl: SignalSafetyController) -> None:
        obs = _obs(phase_elapsed=5.0, phase_remaining=20.0)
        v = ctrl.validate(obs, SignalAction.NEXT_PHASE)
        assert v.safety_override
        assert v.action is SignalAction.HOLD

    def test_next_phase_after_min_green_is_allowed(self, ctrl: SignalSafetyController) -> None:
        obs = _obs(phase_elapsed=MIN_GREEN_S + 1.0, phase_remaining=10.0)
        v = ctrl.validate(obs, SignalAction.NEXT_PHASE)
        assert not v.safety_override
        assert v.action is SignalAction.NEXT_PHASE

    def test_hold_before_min_green_is_always_allowed(self, ctrl: SignalSafetyController) -> None:
        obs = _obs(phase_elapsed=2.0, phase_remaining=23.0)
        v = ctrl.validate(obs, SignalAction.HOLD)
        assert not v.safety_override


class TestMaxGreen:
    def test_hold_at_max_green_is_forced_to_next_phase(self, ctrl: SignalSafetyController) -> None:
        obs = _obs(phase_elapsed=MAX_GREEN_S + 1.0, phase_remaining=0.0)
        v = ctrl.validate(obs, SignalAction.HOLD)
        assert v.safety_override
        assert v.action is SignalAction.NEXT_PHASE

    def test_extend_at_max_green_is_forced_to_next_phase(self, ctrl: SignalSafetyController) -> None:
        obs = _obs(phase_elapsed=MAX_GREEN_S + 5.0, phase_remaining=0.0)
        v = ctrl.validate(obs, SignalAction.EXTEND_GREEN_10)
        assert v.safety_override
        assert v.action is SignalAction.NEXT_PHASE


class TestPedestrianClearance:
    def test_next_phase_with_ped_and_low_remaining_is_held(self, ctrl: SignalSafetyController) -> None:
        obs = _obs(phase_elapsed=20.0, phase_remaining=PED_CLEARANCE_BUFFER_S - 1.0, ped=3)
        v = ctrl.validate(obs, SignalAction.NEXT_PHASE)
        assert v.safety_override
        assert v.action is SignalAction.HOLD

    def test_next_phase_with_ped_and_sufficient_remaining_is_allowed(self, ctrl: SignalSafetyController) -> None:
        obs = _obs(phase_elapsed=20.0, phase_remaining=PED_CLEARANCE_BUFFER_S + 2.0, ped=2)
        v = ctrl.validate(obs, SignalAction.NEXT_PHASE)
        assert not v.safety_override

    def test_no_ped_next_phase_is_unconstrained_by_ped_rule(self, ctrl: SignalSafetyController) -> None:
        obs = _obs(phase_elapsed=MIN_GREEN_S + 5.0, phase_remaining=2.0, ped=0)
        v = ctrl.validate(obs, SignalAction.NEXT_PHASE)
        assert not v.safety_override


class TestExtensionCap:
    def test_extend_near_max_is_blocked(self, ctrl: SignalSafetyController) -> None:
        obs = _obs(phase_elapsed=MAX_GREEN_S - 10.0, phase_remaining=10.0)
        v = ctrl.validate(obs, SignalAction.EXTEND_GREEN_10)
        assert v.safety_override
        assert v.action is SignalAction.HOLD

    def test_extend_well_below_max_is_allowed(self, ctrl: SignalSafetyController) -> None:
        obs = _obs(phase_elapsed=15.0, phase_remaining=20.0)
        v = ctrl.validate(obs, SignalAction.EXTEND_GREEN_5)
        assert not v.safety_override
