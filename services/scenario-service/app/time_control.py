"""
Simulation time controller.

Manages wall-clock vs simulation-time mapping with support for:
  - Variable speed multipliers (0.1x – 10x real time)
  - Pause / resume
  - Deterministic seek (used during replay)

All time values are in seconds (float).
"""

from __future__ import annotations

import time
from typing import Optional


_MIN_MULTIPLIER = 0.1
_MAX_MULTIPLIER = 4.0   # cap at 4× real time; faster causes detector false-negatives


class TimeControlError(Exception):
    """Raised when a time-control operation is invalid in the current state."""


class TimeController:
    """
    Tracks simulation time relative to a wall-clock reference.

    Usage::

        tc = TimeController()
        tc.start()
        # ... in simulation loop:
        sim_dt = tc.advance(real_dt)   # returns the sim-time delta
        current = tc.sim_time_s
    """

    def __init__(self) -> None:
        self._speed_multiplier: float = 1.0
        self._paused: bool = True  # starts paused; call start() to begin
        self._sim_time_s: float = 0.0
        # Wall-clock time of the last resume (used to accumulate sim time).
        self._wall_resume_s: Optional[float] = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Begin the simulation clock from its current sim time."""
        if not self._paused:
            return  # already running
        self._wall_resume_s = time.monotonic()
        self._paused = False

    def pause(self) -> None:
        """Freeze the simulation clock, preserving accumulated sim time."""
        if self._paused:
            return
        # Flush accumulated time before pausing.
        self._flush()
        self._wall_resume_s = None
        self._paused = True

    def resume(self) -> None:
        """Resume the simulation clock after a pause."""
        if not self._paused:
            return
        self._wall_resume_s = time.monotonic()
        self._paused = False

    # ------------------------------------------------------------------
    # Speed & seek
    # ------------------------------------------------------------------

    def set_speed(self, multiplier: float) -> None:
        """
        Set the simulation speed multiplier (0.1 – 10.0).

        Flushes accumulated time first so the new multiplier takes effect
        cleanly from the current sim position.
        """
        if not _MIN_MULTIPLIER <= multiplier <= _MAX_MULTIPLIER:
            raise TimeControlError(
                f"Speed multiplier must be between {_MIN_MULTIPLIER} and "
                f"{_MAX_MULTIPLIER} (max 4× real time), got {multiplier}"
            )
        if not self._paused:
            self._flush()
        self._speed_multiplier = multiplier

    def seek(self, sim_time_s: float) -> None:
        """
        Jump the simulation clock to an arbitrary sim time.

        Used during replay to position the simulation at a specific moment.
        The clock remains in its current running/paused state.
        """
        if sim_time_s < 0.0:
            raise TimeControlError("sim_time_s must be >= 0")
        if not self._paused:
            # Discard any un-flushed time; restart the wall reference.
            self._wall_resume_s = time.monotonic()
        self._sim_time_s = sim_time_s

    # ------------------------------------------------------------------
    # Advance (simulation loop integration)
    # ------------------------------------------------------------------

    def advance(self, dt_s: float) -> float:
        """
        Advance the simulation clock by *dt_s* real seconds.

        Returns the actual sim-time delta applied (0.0 when paused).

        This method is intended for fixed-step simulation loops that supply
        their own delta-time.  Each call flushes the wall-clock reference so
        that wall-clock drift never accumulates on top of the explicit steps.
        """
        if self._paused:
            return 0.0
        sim_delta = dt_s * self._speed_multiplier
        self._sim_time_s += sim_delta
        # Reset the wall reference so the property does not double-count.
        self._wall_resume_s = time.monotonic()
        return sim_delta

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def sim_time_s(self) -> float:
        """Current simulation time in seconds."""
        if self._paused or self._wall_resume_s is None:
            return self._sim_time_s
        # Include time elapsed since last resume.
        elapsed = time.monotonic() - self._wall_resume_s
        return self._sim_time_s + elapsed * self._speed_multiplier

    @property
    def is_paused(self) -> bool:
        return self._paused

    @property
    def speed_multiplier(self) -> float:
        return self._speed_multiplier

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _flush(self) -> None:
        """Accumulate elapsed wall time into _sim_time_s."""
        if self._wall_resume_s is not None:
            elapsed = time.monotonic() - self._wall_resume_s
            self._sim_time_s += elapsed * self._speed_multiplier
            self._wall_resume_s = time.monotonic()
