"""
Unit tests for TimeController.

Tests verify that the time controller correctly tracks simulation time
under various speed settings, pause/resume cycles, and seek operations.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[2] / "services" / "scenario-service"))

from app.time_control import TimeControlError, TimeController


class TestTimeControllerAdvance:
    """Tests for the advance() method (fixed-step mode)."""

    def test_advance_at_1x_speed(self):
        tc = TimeController()
        tc.start()
        delta = tc.advance(1.0)
        assert delta == pytest.approx(1.0)
        assert tc.sim_time_s >= 1.0

    def test_advance_at_2x_speed(self):
        tc = TimeController()
        tc.start()
        tc.set_speed(2.0)
        delta = tc.advance(1.0)
        assert delta == pytest.approx(2.0)

    def test_advance_at_half_speed(self):
        tc = TimeController()
        tc.start()
        tc.set_speed(0.5)
        delta = tc.advance(1.0)
        assert delta == pytest.approx(0.5)

    def test_advance_at_10x_speed(self):
        tc = TimeController()
        tc.start()
        tc.set_speed(10.0)
        delta = tc.advance(5.0)
        assert delta == pytest.approx(50.0)

    def test_advance_accumulates_correctly(self):
        tc = TimeController()
        tc.start()
        tc.set_speed(3.0)
        for _ in range(5):
            tc.advance(1.0)
        # Allow up to 10 ms of wall-clock accumulation between the last advance
        # and the property read.
        assert tc.sim_time_s == pytest.approx(15.0, abs=0.05)

    def test_advance_returns_zero_when_paused(self):
        tc = TimeController()
        # start paused by default; advance() should return 0
        delta = tc.advance(1.0)
        assert delta == 0.0

    def test_speed_multiplier_property(self):
        tc = TimeController()
        tc.set_speed(4.0)
        assert tc.speed_multiplier == pytest.approx(4.0)


class TestTimeControllerPauseResume:
    """Tests for pause/resume behaviour."""

    def test_is_paused_initially(self):
        tc = TimeController()
        assert tc.is_paused is True

    def test_not_paused_after_start(self):
        tc = TimeController()
        tc.start()
        assert tc.is_paused is False

    def test_paused_after_pause(self):
        tc = TimeController()
        tc.start()
        tc.pause()
        assert tc.is_paused is True

    def test_not_paused_after_resume(self):
        tc = TimeController()
        tc.start()
        tc.pause()
        tc.resume()
        assert tc.is_paused is False

    def test_sim_time_does_not_advance_while_paused(self):
        tc = TimeController()
        tc.start()
        tc.advance(10.0)
        tc.pause()
        snapshot = tc.sim_time_s
        # Even if we call advance while paused it should not move.
        delta = tc.advance(5.0)
        assert delta == 0.0
        assert tc.sim_time_s == pytest.approx(snapshot)

    def test_sim_time_resumes_from_correct_position(self):
        tc = TimeController()
        tc.start()
        tc.advance(10.0)
        tc.pause()
        paused_snapshot = tc.sim_time_s
        tc.resume()
        tc.advance(5.0)
        assert tc.sim_time_s == pytest.approx(paused_snapshot + 5.0)

    def test_multiple_pause_resume_cycles(self):
        tc = TimeController()
        tc.start()
        tc.set_speed(1.0)
        tc.advance(5.0)
        tc.pause()
        tc.resume()
        tc.advance(5.0)
        tc.pause()
        tc.resume()
        tc.advance(5.0)
        # Allow up to 50 ms of wall-clock accumulation across three resume cycles.
        assert tc.sim_time_s == pytest.approx(15.0, abs=0.05)

    def test_start_idempotent(self):
        tc = TimeController()
        tc.start()
        tc.start()  # calling start twice should not raise
        assert tc.is_paused is False

    def test_pause_idempotent(self):
        tc = TimeController()
        tc.start()
        tc.pause()
        tc.pause()  # calling pause twice should not raise
        assert tc.is_paused is True

    def test_resume_idempotent(self):
        tc = TimeController()
        tc.start()
        tc.resume()  # already running
        assert tc.is_paused is False


class TestTimeControllerSpeed:
    """Tests for set_speed() validation and effect on advance()."""

    def test_speed_below_minimum_raises(self):
        tc = TimeController()
        with pytest.raises(TimeControlError):
            tc.set_speed(0.0)

    def test_speed_above_maximum_raises(self):
        tc = TimeController()
        with pytest.raises(TimeControlError):
            tc.set_speed(10.1)

    def test_speed_at_minimum_valid(self):
        tc = TimeController()
        tc.set_speed(0.1)
        assert tc.speed_multiplier == pytest.approx(0.1)

    def test_speed_at_maximum_valid(self):
        tc = TimeController()
        tc.set_speed(10.0)
        assert tc.speed_multiplier == pytest.approx(10.0)

    def test_speed_change_mid_run(self):
        """Changing speed mid-run should not discard accumulated sim time."""
        tc = TimeController()
        tc.start()
        tc.set_speed(1.0)
        tc.advance(10.0)
        snapshot = tc.sim_time_s
        tc.set_speed(2.0)
        tc.advance(5.0)
        assert tc.sim_time_s == pytest.approx(snapshot + 10.0)


class TestTimeControllerSeek:
    """Tests for the seek() operation (replay positioning)."""

    def test_seek_sets_sim_time(self):
        tc = TimeController()
        tc.start()
        tc.seek(120.0)
        assert tc.sim_time_s == pytest.approx(120.0)

    def test_seek_backward(self):
        tc = TimeController()
        tc.start()
        tc.advance(200.0)
        tc.seek(50.0)
        assert tc.sim_time_s == pytest.approx(50.0)

    def test_seek_to_zero(self):
        tc = TimeController()
        tc.start()
        tc.advance(100.0)
        tc.seek(0.0)
        # After seek the clock is running again; allow up to 10 ms of wall-clock
        # accumulation between seek() and the property read.
        assert tc.sim_time_s == pytest.approx(0.0, abs=0.05)

    def test_seek_negative_raises(self):
        tc = TimeController()
        tc.start()
        with pytest.raises(TimeControlError):
            tc.seek(-1.0)

    def test_seek_preserves_running_state(self):
        tc = TimeController()
        tc.start()
        tc.seek(60.0)
        assert tc.is_paused is False

    def test_seek_while_paused_preserves_paused_state(self):
        tc = TimeController()
        # Controller starts paused
        tc.seek(60.0)
        assert tc.is_paused is True
        assert tc.sim_time_s == pytest.approx(60.0)

    def test_advance_after_seek_continues_from_seek_position(self):
        tc = TimeController()
        tc.start()
        tc.seek(100.0)
        tc.advance(10.0)
        assert tc.sim_time_s == pytest.approx(110.0)
