"""
SimulationRunner: drives the simulation tick loop.

Decoupled from any specific adapter implementation — it accepts any object
that satisfies the SimulationAdapter Protocol, including mocks.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Callable, Optional

from pydantic import BaseModel

from .base import SimulationAdapter
from .schemas import CanonicalEvent, PedestrianState, VehicleState

log = logging.getLogger(__name__)


class RunnerStats(BaseModel):
    """Runtime statistics collected by SimulationRunner."""

    tick_count: int = 0
    event_count: int = 0
    error_count: int = 0
    started_at: Optional[datetime] = None
    last_tick_at: Optional[datetime] = None


class SimulationRunner:
    """
    Drives the simulation tick loop.

    Responsibilities:
    - Call adapter.step() at the configured frequency
    - Collect canonical events from each tick
    - Deliver events to the provided callback
    - Track runtime statistics

    The runner is decoupled from the adapter: any object satisfying
    SimulationAdapter (traci, libsumo, mock, real sensor) can be used.
    """

    def __init__(
        self,
        adapter: SimulationAdapter,
        world_state_callback: Callable[[list[CanonicalEvent]], None],
        tick_hz: float = 10.0,
    ) -> None:
        """
        Args:
            adapter: A SimulationAdapter instance (traci, libsumo, or mock).
            world_state_callback: Called with a list of CanonicalEvent after each tick.
            tick_hz: Target simulation frequency in Hz (default 10 Hz → 100 ms/tick).
        """
        self._adapter = adapter
        self._callback = world_state_callback
        self._tick_hz = tick_hz
        self._dt = 1.0 / tick_hz
        self._running = False
        self._stats = RunnerStats()

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    async def run(self, config: dict, scenario_run_id: str) -> None:
        """
        Main async run loop.

        Resets the adapter, starts the simulation, then drives the tick loop
        until stop() is called or the adapter raises an exception.
        """
        self._adapter.reset(scenario_run_id)
        self._adapter.start(config)
        self._running = True
        self._stats = RunnerStats(started_at=datetime.now(timezone.utc))

        log.info(
            "SimulationRunner started: scenario_run_id=%s tick_hz=%.1f",
            scenario_run_id,
            self._tick_hz,
        )

        try:
            while self._running:
                tick_start = time.monotonic()

                try:
                    events = self._tick()
                except Exception as exc:
                    log.error("Tick error: %s", exc, exc_info=True)
                    self._stats.error_count += 1
                    events = []

                try:
                    self._callback(events)
                except Exception as exc:
                    log.error("Callback error: %s", exc, exc_info=True)

                elapsed = time.monotonic() - tick_start
                sleep_time = max(0.0, self._dt - elapsed)
                await asyncio.sleep(sleep_time)
        finally:
            self._adapter.stop()
            self._running = False
            log.info(
                "SimulationRunner stopped: ticks=%d events=%d errors=%d",
                self._stats.tick_count,
                self._stats.event_count,
                self._stats.error_count,
            )

    # ------------------------------------------------------------------
    # Single tick
    # ------------------------------------------------------------------

    def _tick(self) -> list[CanonicalEvent]:
        """
        Execute a single simulation step and return canonical events.

        This method is synchronous and safe to call directly in tests
        (bypassing the async run loop).
        """
        self._adapter.step(self._dt)
        now = datetime.now(timezone.utc)
        events: list[CanonicalEvent] = []

        # --- Actor states ---
        for actor in self._adapter.list_actors():
            if isinstance(actor, VehicleState):
                events.append(
                    CanonicalEvent(
                        event_type="actor.state.updated",
                        timestamp_utc=now,
                        source=actor.source,
                        trace_id=actor.trace_id,
                        payload=actor.model_dump(),
                    )
                )
            elif isinstance(actor, PedestrianState):
                events.append(
                    CanonicalEvent(
                        event_type="actor.state.updated",
                        timestamp_utc=now,
                        source=actor.source,
                        trace_id=actor.trace_id,
                        payload=actor.model_dump(),
                    )
                )
            # DynamicActorObservation or unknown types are silently skipped
            # (they carry no trace_id and we do not want to lose the event)
            else:
                pass

        # --- Signal states ---
        for signal in self._adapter.get_signal_states():
            events.append(
                CanonicalEvent(
                    event_type="infrastructure.signal.updated",
                    timestamp_utc=now,
                    source=signal.source,
                    trace_id=str(uuid.uuid4()),
                    payload=signal.model_dump(),
                )
            )

        self._stats.tick_count += 1
        self._stats.event_count += len(events)
        self._stats.last_tick_at = now

        return events

    # ------------------------------------------------------------------
    # Control
    # ------------------------------------------------------------------

    def stop(self) -> None:
        """Signal the run loop to stop after the current tick."""
        self._running = False

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def stats(self) -> RunnerStats:
        return self._stats
