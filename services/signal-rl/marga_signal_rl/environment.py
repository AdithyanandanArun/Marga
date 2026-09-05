"""Mock SUMO signal environment for RL training without a live SUMO process.

Models a single 4-way signalised junction with realistic demand patterns.
The SumoTraciSignalEnvironment subclass wraps the real traci connection
and is used when SUMO is available. Both expose the same step() interface
so the trainer is backend-agnostic.
"""

from __future__ import annotations

import logging
import random
from typing import Optional

from .actions import ACTION_EXTENSION_S, SignalAction
from .reward import compute_reward
from .safety import SignalSafetyController
from .state import ApproachState, SignalObservation

log = logging.getLogger(__name__)

# Phase programme for a standard 4-way intersection.
# Each phase has a movement map and a default duration.
_PHASE_PROGRAMME: list[dict] = [
    {
        "id": "NS_GREEN",
        "movements": {"N": "GREEN", "S": "GREEN", "E": "RED", "W": "RED"},
        "default_s": 25.0,
        "min_s": 10.0,
    },
    {
        "id": "ALL_RED_1",
        "movements": {"N": "RED", "S": "RED", "E": "RED", "W": "RED"},
        "default_s": 2.0,
        "min_s": 2.0,
    },
    {
        "id": "EW_GREEN",
        "movements": {"N": "RED", "S": "RED", "E": "GREEN", "W": "GREEN"},
        "default_s": 25.0,
        "min_s": 10.0,
    },
    {
        "id": "ALL_RED_2",
        "movements": {"N": "RED", "S": "RED", "E": "RED", "W": "RED"},
        "default_s": 2.0,
        "min_s": 2.0,
    },
]

# Baseline vehicle arrival demand per direction (vehicles/min)
_DEMAND_BASE: dict[str, float] = {"N": 4.5, "S": 3.0, "E": 6.0, "W": 2.5}
_APPROACH_LENGTH_M: float = 500.0  # assumed approach length for density calc


class MockSumoSignalEnvironment:
    """
    Pure-Python mock of a SUMO signalised junction.

    Suitable for offline RL training. Traffic dynamics are intentionally simple
    but capture the key trade-offs: queue accumulation on red, discharge on green,
    pedestrian crossings, and demand variability.
    """

    STEP_S: float = 5.0          # decision interval
    EPISODE_DURATION_S: float = 3600.0  # 1-hour episodes

    def __init__(self, junction_id: str = "mock-junction", seed: int = 42) -> None:
        self.junction_id = junction_id
        self.safety = SignalSafetyController()
        self._rng = random.Random(seed)
        self._phases = _PHASE_PROGRAMME
        self._reset_state()

    def _reset_state(self) -> None:
        self._phase_index = 0
        self._phase_elapsed = 0.0
        self._phase_duration = self._phases[0]["default_s"]
        self._clock = 0.0
        self._queues: dict[str, float] = {d: float(self._rng.randint(3, 15)) for d in "NSEW"}
        self._speeds: dict[str, float] = {
            "N": self._rng.uniform(2.0, 6.0),
            "S": self._rng.uniform(2.5, 7.0),
            "E": self._rng.uniform(1.5, 5.0),
            "W": self._rng.uniform(4.0, 8.0),
        }
        self._ped: dict[str, int] = {d: self._rng.randint(0, 4) for d in "NSEW"}
        # Demand varies between episodes (±30 %)
        scale = self._rng.uniform(0.7, 1.3)
        self._demand = {d: v * scale for d, v in _DEMAND_BASE.items()}

    def reset(self, seed: Optional[int] = None) -> SignalObservation:
        if seed is not None:
            self._rng = random.Random(seed)
        self._reset_state()
        return self._make_obs()

    # ------------------------------------------------------------------
    # Core step
    # ------------------------------------------------------------------

    def step(self, action: SignalAction) -> tuple[SignalObservation, float, bool]:
        obs_before = self._make_obs()

        verdict = self.safety.validate(obs_before, action)
        effective = verdict.action

        # Apply action to signal state
        if effective is SignalAction.NEXT_PHASE:
            self._advance_phase()
        elif effective in (SignalAction.EXTEND_GREEN_5, SignalAction.EXTEND_GREEN_10):
            self._phase_duration += ACTION_EXTENSION_S[effective]
        # HOLD: no change to phase timing

        # Simulate traffic for STEP_S seconds
        self._simulate_traffic(self.STEP_S)

        obs_after = self._make_obs()
        reward = compute_reward(obs_before, obs_after)
        done = self._clock >= self.EPISODE_DURATION_S

        return obs_after, reward, done

    def current_obs(self) -> SignalObservation:
        return self._make_obs()

    # ------------------------------------------------------------------
    # Internal mechanics
    # ------------------------------------------------------------------

    def _advance_phase(self) -> None:
        self._phase_index = (self._phase_index + 1) % len(self._phases)
        self._phase_elapsed = 0.0
        self._phase_duration = float(self._phases[self._phase_index]["default_s"])

    def _simulate_traffic(self, dt: float) -> None:
        self._clock += dt
        self._phase_elapsed += dt
        # Auto-advance when scheduled duration expires
        if self._phase_elapsed >= self._phase_duration:
            self._advance_phase()

        green_dirs = {
            d for d, colour in self._phases[self._phase_index]["movements"].items() if colour == "GREEN"
        }

        for direction in "NSEW":
            if direction in green_dirs:
                # Discharge up to 3 vehicles per 5-second step (saturated flow ~2100 veh/h/lane)
                discharged = min(
                    int(self._queues[direction]),
                    self._rng.randint(0, 3),
                )
                self._queues[direction] = max(0.0, self._queues[direction] - discharged)
                self._speeds[direction] = min(10.0, self._speeds[direction] + self._rng.uniform(0.3, 1.2))
                self._ped[direction] = max(0, self._ped[direction] - 1)
            else:
                arrived = self._demand[direction] * (dt / 60.0) * self._rng.uniform(0.7, 1.4)
                self._queues[direction] = min(35.0, self._queues[direction] + arrived)
                self._speeds[direction] = max(0.3, self._speeds[direction] - self._rng.uniform(0.1, 0.6))
                if self._rng.random() < 0.08:
                    self._ped[direction] += 1

    def _make_obs(self) -> SignalObservation:
        phase = self._phases[self._phase_index]
        remaining = max(0.0, self._phase_duration - self._phase_elapsed)
        approaches: dict[str, ApproachState] = {}
        for direction in "NSEW":
            q = int(self._queues[direction])
            approaches[direction] = ApproachState(
                movement_id=direction,
                queue_length=q,
                density=q / (_APPROACH_LENGTH_M / 1000.0),
                avg_speed_mps=round(self._speeds[direction], 2),
                incoming_flow=round(self._demand[direction], 2),
                downstream_occupancy=min(1.0, q / 25.0),
                pedestrian_demand=self._ped[direction],
                vru_density=self._ped[direction] / _APPROACH_LENGTH_M,
            )
        return SignalObservation(
            junction_id=self.junction_id,
            current_phase=phase["id"],
            phase_index=self._phase_index,
            phase_elapsed_s=round(self._phase_elapsed, 2),
            phase_remaining_s=round(remaining, 2),
            approaches=approaches,
            ts=f"T+{self._clock:.1f}s",
        )


class SumoTraciSignalEnvironment:
    """
    Live-SUMO environment via TraCI. Used when a SUMO process is running.

    Falls back to MockSumoSignalEnvironment if traci is not installed.
    """

    def __init__(self, junction_id: str, traci_host: str = "localhost", traci_port: int = 8813) -> None:
        self.junction_id = junction_id
        self._host = traci_host
        self._port = traci_port
        self.safety = SignalSafetyController()
        self._conn = None
        self._clock = 0.0

    def connect(self) -> None:
        try:
            import traci  # type: ignore[import]

            self._conn = traci.connect(host=self._host, port=self._port)
            log.info("Connected to SUMO via TraCI at %s:%d", self._host, self._port)
        except ImportError:
            raise RuntimeError("traci is not installed — use MockSumoSignalEnvironment for training")

    def disconnect(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None

    def step(self, action: SignalAction) -> tuple[SignalObservation, float, bool]:
        assert self._conn is not None, "call connect() first"
        obs_before = self._get_obs()
        verdict = self.safety.validate(obs_before, action)
        effective = verdict.action

        self._apply_to_sumo(effective, obs_before)
        self._conn.simulationStep()
        self._clock += 1.0

        obs_after = self._get_obs()
        reward = compute_reward(obs_before, obs_after)
        done = self._conn.simulation.getMinExpectedNumber() == 0
        return obs_after, reward, done

    def _apply_to_sumo(self, action: SignalAction, obs: SignalObservation) -> None:
        jid = self.junction_id
        if action is SignalAction.NEXT_PHASE:
            next_idx = (obs.phase_index + 1) % 4
            self._conn.trafficlight.setPhase(jid, next_idx)
        elif action is SignalAction.EXTEND_GREEN_5:
            self._conn.trafficlight.setPhaseDuration(jid, obs.phase_remaining_s + 5.0)
        elif action is SignalAction.EXTEND_GREEN_10:
            self._conn.trafficlight.setPhaseDuration(jid, obs.phase_remaining_s + 10.0)
        # HOLD: nothing

    def _get_obs(self) -> SignalObservation:
        jid = self.junction_id
        phase_idx = self._conn.trafficlight.getPhase(jid)
        phase_str = self._conn.trafficlight.getRedYellowGreenState(jid)
        remaining = max(0.0, self._conn.trafficlight.getNextSwitch(jid) - self._conn.simulation.getTime())
        elapsed = self._clock  # approximate; precise value requires phase start tracking

        approaches: dict[str, ApproachState] = {}
        for lane_id in self._conn.trafficlight.getControlledLanes(jid):
            edge_id = lane_id.rsplit("_", 1)[0]
            direction = edge_id[-1].upper() if edge_id else "X"
            q = self._conn.lane.getLastStepHaltingNumber(lane_id)
            speed = self._conn.lane.getLastStepMeanSpeed(lane_id)
            if direction not in approaches:
                approaches[direction] = ApproachState(
                    movement_id=direction,
                    queue_length=q,
                    density=q / 0.5,
                    avg_speed_mps=speed,
                    incoming_flow=self._conn.lane.getLastStepVehicleNumber(lane_id) * 12.0,
                    downstream_occupancy=self._conn.lane.getLastStepOccupancy(lane_id),
                    pedestrian_demand=0,
                    vru_density=0.0,
                )

        return SignalObservation(
            junction_id=jid,
            current_phase=phase_str,
            phase_index=phase_idx,
            phase_elapsed_s=elapsed,
            phase_remaining_s=remaining,
            approaches=approaches,
            ts=f"T+{self._clock:.1f}s",
        )
