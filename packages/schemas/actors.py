"""Dynamic actor schemas: vehicles, pedestrians, and other moving entities."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator

from .common import PositionEstimate


class VehicleType(str, Enum):
    """Road-user categories relevant to the Indian road context."""

    car = "car"
    truck = "truck"
    bus = "bus"
    motorcycle = "motorcycle"
    auto_rickshaw = "auto_rickshaw"
    bicycle = "bicycle"
    tractor = "tractor"
    emergency = "emergency"


class VehicleState(BaseModel):
    """Instantaneous kinematic state of a single vehicle."""

    schema_version: str = Field("1.0", description="Schema version string")
    vehicle_id: str = Field(..., description="Unique vehicle identifier")
    timestamp_utc: datetime = Field(..., description="UTC timestamp of observation")
    position: PositionEstimate = Field(..., description="Position with uncertainty")
    speed_mps: float = Field(..., description="Speed in metres per second (>= 0)")
    heading_deg: float = Field(
        ..., description="Heading in degrees clockwise from true north (0-360)"
    )
    acceleration_mps2: Optional[float] = Field(
        None, description="Longitudinal acceleration in m/s²"
    )
    vehicle_type: VehicleType = Field(..., description="Vehicle class")
    source: str = Field(
        ...,
        description="Data origin: 'sumo_traci', 'sumo_libsumo', 'gnss', or 'obu'",
    )
    scenario_run_id: Optional[str] = Field(None, description="Simulation scenario run identifier")
    trace_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Distributed trace identifier (UUID)",
    )

    @field_validator("speed_mps")
    @classmethod
    def speed_non_negative(cls, v: float) -> float:
        if v < 0:
            raise ValueError(f"speed_mps must be >= 0, got {v}")
        return v

    @field_validator("heading_deg")
    @classmethod
    def heading_in_range(cls, v: float) -> float:
        if not (0.0 <= v <= 360.0):
            raise ValueError(f"heading_deg must be in [0, 360], got {v}")
        return v


class PedestrianState(BaseModel):
    """Instantaneous kinematic state of a pedestrian."""

    schema_version: str = Field("1.0", description="Schema version string")
    pedestrian_id: str = Field(..., description="Unique pedestrian identifier")
    timestamp_utc: datetime = Field(..., description="UTC timestamp of observation")
    position: PositionEstimate = Field(..., description="Position with uncertainty")
    speed_mps: float = Field(..., ge=0.0, description="Speed in metres per second")
    heading_deg: float = Field(
        ..., description="Heading in degrees clockwise from true north (0-360)"
    )
    source: str = Field(..., description="Data origin identifier")
    scenario_run_id: Optional[str] = Field(None, description="Simulation scenario run identifier")
    trace_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Distributed trace identifier (UUID)",
    )

    @field_validator("heading_deg")
    @classmethod
    def heading_in_range(cls, v: float) -> float:
        if not (0.0 <= v <= 360.0):
            raise ValueError(f"heading_deg must be in [0, 360], got {v}")
        return v


class DynamicActorObservation(BaseModel):
    """Observation of a non-vehicle dynamic actor (animals, debris, groups, etc.)."""

    schema_version: str = Field("1.0", description="Schema version string")
    actor_id: str = Field(..., description="Unique actor identifier")
    timestamp_utc: datetime = Field(..., description="UTC timestamp of observation")
    actor_type: str = Field(
        ...,
        description=(
            "Actor category: 'animal', 'debris', 'pedestrian_group', 'cyclist', 'cart'"
        ),
    )
    position: PositionEstimate = Field(..., description="Position with uncertainty")
    confidence: float = Field(..., description="Detection confidence in [0, 1]")
    source: str = Field(..., description="Sensor or system that produced this observation")
    scenario_run_id: Optional[str] = Field(None, description="Simulation scenario run identifier")

    @field_validator("confidence")
    @classmethod
    def confidence_in_range(cls, v: float) -> float:
        if not (0.0 <= v <= 1.0):
            raise ValueError(f"confidence must be in [0, 1], got {v}")
        return v
