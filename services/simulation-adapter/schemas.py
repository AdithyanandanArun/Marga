"""
Canonical schema types for the Marga V2X simulation adapter.

This is the adapter's local copy of canonical schemas.
It does NOT depend on an external shared package.
SUMO-specific types must NOT leak into this module.
"""

from pydantic import BaseModel, field_validator
from typing import Optional
from datetime import datetime, timezone
from enum import Enum
import uuid


class Position(BaseModel):
    lat: float
    lon: float
    alt_m: Optional[float] = None


class PositionEstimate(BaseModel):
    lat: float
    lon: float
    alt_m: Optional[float] = None
    uncertainty_m: float = 0.0
    confidence: float = 1.0  # 0-1
    source: str = "sumo"

    @field_validator("confidence")
    @classmethod
    def validate_confidence(cls, v: float) -> float:
        if not 0.0 <= v <= 1.0:
            raise ValueError(f"confidence must be 0-1, got {v}")
        return v


class VehicleType(str, Enum):
    car = "car"
    truck = "truck"
    bus = "bus"
    motorcycle = "motorcycle"
    auto_rickshaw = "auto_rickshaw"
    bicycle = "bicycle"
    tractor = "tractor"
    emergency = "emergency"


class VehicleState(BaseModel):
    schema_version: str = "1.0"
    vehicle_id: str
    timestamp_utc: datetime
    position: PositionEstimate
    speed_mps: float
    heading_deg: float  # 0-360, clockwise from true north
    acceleration_mps2: Optional[float] = None
    vehicle_type: VehicleType = VehicleType.car
    source: str = "sumo_traci"
    scenario_run_id: Optional[str] = None
    trace_id: str = ""

    def model_post_init(self, __context: object) -> None:
        if not self.trace_id:
            self.trace_id = str(uuid.uuid4())


class PedestrianState(BaseModel):
    schema_version: str = "1.0"
    pedestrian_id: str
    timestamp_utc: datetime
    position: PositionEstimate
    speed_mps: float
    heading_deg: float
    source: str = "sumo_traci"
    scenario_run_id: Optional[str] = None
    trace_id: str = ""

    def model_post_init(self, __context: object) -> None:
        if not self.trace_id:
            self.trace_id = str(uuid.uuid4())


class SignalPhase(str, Enum):
    red = "red"
    yellow = "yellow"
    green = "green"
    flashing_red = "flashing_red"
    flashing_yellow = "flashing_yellow"
    off = "off"


class InfrastructureState(BaseModel):
    schema_version: str = "1.0"
    infrastructure_id: str
    timestamp_utc: datetime
    infrastructure_type: str = "traffic_signal"
    position: Position
    signal_phase: Optional[SignalPhase] = None
    phase_remaining_s: Optional[float] = None
    operational: bool = True
    source: str = "sumo_traci"
    scenario_run_id: Optional[str] = None


class RoadCondition(str, Enum):
    clear = "clear"
    wet = "wet"
    construction = "construction"
    closed = "closed"


class RoadState(BaseModel):
    schema_version: str = "1.0"
    edge_id: str
    timestamp_utc: datetime
    lanes_available: int
    total_lanes: int
    speed_limit_mps: float
    road_condition: RoadCondition = RoadCondition.clear
    source: str = "sumo_traci"
    scenario_run_id: Optional[str] = None


class DynamicActorObservation(BaseModel):
    schema_version: str = "1.0"
    actor_id: str
    timestamp_utc: datetime
    actor_type: str
    position: PositionEstimate
    confidence: float = 1.0
    source: str = "sumo_traci"
    scenario_run_id: Optional[str] = None


class CanonicalEvent(BaseModel):
    event_type: str
    schema_version: str = "1.0"
    event_id: str = ""
    timestamp_utc: datetime
    source: str
    trace_id: str = ""
    payload: dict

    def model_post_init(self, __context: object) -> None:
        if not self.event_id:
            self.event_id = str(uuid.uuid4())
        if not self.trace_id:
            self.trace_id = str(uuid.uuid4())
