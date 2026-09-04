"""
Canonical types for the Marga scenario service.

All scenario definitions, run states, and failure injection types are defined here
and used as the single source of truth across the service.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, field_validator


class FailureType(str, Enum):
    gps_degradation = "gps_degradation"
    connectivity_loss = "connectivity_loss"
    rsu_failure = "rsu_failure"
    traffic_density_spike = "traffic_density_spike"
    weather_visibility = "weather_visibility"
    malicious_input = "malicious_input"
    road_closure = "road_closure"
    road_narrowing = "road_narrowing"
    animal_crossing = "animal_crossing"
    emergency_vehicle = "emergency_vehicle"
    wrong_way_vehicle = "wrong_way_vehicle"
    stalled_vehicle = "stalled_vehicle"


class FailureScheduleEntry(BaseModel):
    """A single scheduled failure event in a scenario."""

    entry_id: str = ""
    failure_type: FailureType
    start_sim_time_s: float
    duration_s: Optional[float] = None  # None = until scenario ends
    parameters: dict[str, Any] = {}
    # Examples of parameters by type:
    # gps_degradation: {"uncertainty_m": 50.0, "affected_actors": ["all"] or ["veh1", "veh2"]}
    # connectivity_loss: {"affected_services": ["world_state", "alerts"]}
    # rsu_failure: {"rsu_id": "rsu_123"}
    # traffic_density_spike: {"edge_ids": ["edge1"], "density_factor": 2.0}
    # weather_visibility: {"visibility_m": 100.0, "condition": "fog"}
    # malicious_input: {"actor_id": "malicious_1", "payload_type": "spoofed_position"}
    # road_closure: {"edge_id": "edge_abc"}
    # animal_crossing: {"edge_id": "edge_abc", "actor_type": "cow", "count": 3}

    def model_post_init(self, __context: Any) -> None:
        if not self.entry_id:
            self.entry_id = str(uuid.uuid4())


class TrafficComposition(BaseModel):
    """Defines the mix of vehicle types for a scenario."""

    car_fraction: float = 0.6
    truck_fraction: float = 0.1
    bus_fraction: float = 0.05
    motorcycle_fraction: float = 0.15
    auto_rickshaw_fraction: float = 0.08
    bicycle_fraction: float = 0.02
    pedestrian_density: float = 0.5  # 0-1

    @field_validator(
        "car_fraction",
        "truck_fraction",
        "bus_fraction",
        "motorcycle_fraction",
        "auto_rickshaw_fraction",
        "bicycle_fraction",
        "pedestrian_density",
    )
    @classmethod
    def validate_fraction(cls, v: float) -> float:
        if not 0.0 <= v <= 1.0:
            raise ValueError(f"Fractions must be 0-1, got {v}")
        return v


class EnvironmentConditions(BaseModel):
    """Ambient environmental conditions for a scenario."""

    time_of_day_s: float = 28800.0  # seconds since midnight (28800 = 8am)
    visibility_m: float = 10000.0  # clear
    precipitation: str = "none"  # none, light_rain, heavy_rain, fog
    road_wetness: float = 0.0  # 0-1
    wind_speed_mps: float = 0.0


class ScenarioDefinition(BaseModel):
    """The full definition of a reproducible scenario."""

    schema_version: str = "1.0"
    scenario_id: str = ""
    name: str
    description: str = ""
    osm_region: str  # region name matching what OSM import produces
    seed: int
    duration_s: float = 300.0  # 5 minutes default
    traffic_composition: TrafficComposition = TrafficComposition()
    environment: EnvironmentConditions = EnvironmentConditions()
    failure_schedule: list[FailureScheduleEntry] = []
    sumo_net_file: Optional[str] = None  # overrides region default
    sumo_route_file: Optional[str] = None
    tags: list[str] = []
    created_at: datetime = datetime.now(timezone.utc)

    def model_post_init(self, __context: Any) -> None:
        if not self.scenario_id:
            self.scenario_id = str(uuid.uuid4())


class ScenarioRunState(str, Enum):
    pending = "pending"
    running = "running"
    paused = "paused"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


class ScenarioRun(BaseModel):
    """Runtime state of a scenario execution."""

    schema_version: str = "1.0"
    run_id: str = ""
    scenario_id: str
    state: ScenarioRunState = ScenarioRunState.pending
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    current_sim_time_s: float = 0.0
    speed_multiplier: float = 1.0
    actor_count: int = 0
    event_count: int = 0
    active_failures: list[str] = []  # list of entry_ids

    def model_post_init(self, __context: Any) -> None:
        if not self.run_id:
            self.run_id = str(uuid.uuid4())


class PositionEstimate(BaseModel):
    """GPS/position estimate for an actor, used by failure injection."""

    actor_id: str
    latitude: float
    longitude: float
    altitude_m: float = 0.0
    uncertainty_m: float = 5.0  # horizontal position uncertainty
    confidence: float = 1.0  # 0.0-1.0
    heading_deg: Optional[float] = None
    speed_mps: Optional[float] = None
    timestamp: datetime = datetime.now(timezone.utc)

    @field_validator("confidence")
    @classmethod
    def clamp_confidence(cls, v: float) -> float:
        return max(0.0, min(1.0, v))


class SpeedRequest(BaseModel):
    """Request body for setting run speed multiplier."""

    multiplier: float

    @field_validator("multiplier")
    @classmethod
    def validate_multiplier(cls, v: float) -> float:
        if not 0.1 <= v <= 10.0:
            raise ValueError("Speed multiplier must be between 0.1 and 10.0")
        return v


class InjectFailureRequest(BaseModel):
    """Request body for injecting a one-off failure into a running scenario."""

    failure_type: FailureType
    duration_s: Optional[float] = None
    parameters: dict[str, Any] = {}
