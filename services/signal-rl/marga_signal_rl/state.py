"""Signal observation state consumed from the mobility graph and SUMO."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ApproachState:
    """Per-approach (movement direction) metrics from the live mobility graph."""

    movement_id: str
    queue_length: int           # vehicles queued at stop line
    density: float              # vehicles / km / lane
    avg_speed_mps: float
    incoming_flow: float        # vehicles / min arriving from upstream
    downstream_occupancy: float  # [0, 1] — how full the downstream edge is
    pedestrian_demand: int      # pedestrians waiting to cross
    vru_density: float          # VRU count per metre of approach


@dataclass
class SignalObservation:
    """Full junction observation snapshot fed to the RL agent."""

    junction_id: str
    current_phase: str          # e.g. "NS_GREEN"
    phase_index: int            # index into the junction's phase programme
    phase_elapsed_s: float      # seconds since current phase began
    phase_remaining_s: float    # seconds until scheduled phase end
    approaches: dict[str, ApproachState]   # movement_id → ApproachState
    ts: str                     # ISO-8601 or sim clock string

    def total_queue(self) -> int:
        return sum(a.queue_length for a in self.approaches.values())

    def avg_speed(self) -> float:
        if not self.approaches:
            return 0.0
        return sum(a.avg_speed_mps for a in self.approaches.values()) / len(self.approaches)

    def total_ped_demand(self) -> int:
        return sum(a.pedestrian_demand for a in self.approaches.values())

    def max_queue_approach(self) -> str | None:
        if not self.approaches:
            return None
        return max(self.approaches, key=lambda k: self.approaches[k].queue_length)

    def total_flow(self) -> float:
        return sum(a.incoming_flow for a in self.approaches.values())
