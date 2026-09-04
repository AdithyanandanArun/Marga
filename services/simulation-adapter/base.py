"""
SimulationAdapter Protocol and AdapterConfig.

SimulationAdapter is a runtime-checkable Protocol so any class that
implements the required methods satisfies it — no explicit inheritance needed.
This makes it possible to swap traci, libsumo, or a real sensor adapter
without changing any downstream code.
"""

from typing import Protocol, runtime_checkable, Iterable, Union, Optional
from pydantic import BaseModel

from .schemas import (
    VehicleState,
    PedestrianState,
    InfrastructureState,
    RoadState,
    DynamicActorObservation,
)


@runtime_checkable
class SimulationAdapter(Protocol):
    """
    Protocol for simulation backends (traci, libsumo, mock, real-sensor).

    All methods must be implemented by the concrete adapter.
    Switching backends must not change the canonical output schema.
    """

    def start(self, config: dict) -> None:
        """Start the simulation with the given configuration dict."""
        ...

    def stop(self) -> None:
        """Stop the simulation and release resources."""
        ...

    def step(self, dt: float) -> None:
        """Advance simulation by ``dt`` seconds."""
        ...

    def list_actors(
        self,
    ) -> Iterable[Union[VehicleState, PedestrianState, DynamicActorObservation]]:
        """Yield all currently active actors as canonical state objects."""
        ...

    def get_signal_states(self) -> list[InfrastructureState]:
        """Return current state of all traffic signals."""
        ...

    def get_road_states(self) -> list[RoadState]:
        """Return current road states (changed edges only is acceptable)."""
        ...

    def apply_vehicle_command(self, command: dict) -> None:
        """
        Apply a vehicle control command.

        Expected keys:
            vehicle_id (str): target vehicle
            action (str): "setSpeed" | "setRoute" | "remove"
            value (Any): action-specific value
        """
        ...

    def apply_signal_command(self, command: dict) -> None:
        """
        Override a traffic signal.

        Expected keys:
            signal_id (str): target TLS ID
            phase (str, optional): SUMO phase string to set
            duration_s (float, optional): duration for this phase
        """
        ...

    def apply_road_event(self, event: dict) -> None:
        """
        Apply a road event (closure, speed limit change, etc.).

        Expected keys:
            edge_id (str): target edge
            event_type (str): "close" | "narrow" | "speed_limit"
            value (Any): event-specific value
        """
        ...

    def reset(self, scenario_run_id: str) -> None:
        """Stop and restart with a new scenario run ID."""
        ...

    @property
    def current_time(self) -> float:
        """Current simulation time in seconds."""
        ...

    @property
    def scenario_run_id(self) -> str:
        """Identifier for the current simulation run."""
        ...


class AdapterConfig(BaseModel):
    """Configuration for the SUMO simulation adapter."""

    sumo_net_file: str
    sumo_route_file: str
    sumo_config_file: Optional[str] = None
    step_length_s: float = 0.1
    coordinate_system: str = "wgs84"  # or "sumo_cartesian"
    sumo_origin_lat: Optional[float] = None  # For coordinate conversion
    sumo_origin_lon: Optional[float] = None
    seed: int = 42
    gui: bool = False
    port: int = 8813
    v2x_range_m: float = 300.0  # Max V2X/RSU communication range in metres (India DSRC ~300 m)
