"""Road state and event schemas for SUMO/OSM edge-level road conditions."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator


class RoadCondition(str, Enum):
    """Surface and access conditions for a road edge."""

    clear = "clear"
    wet = "wet"
    construction = "construction"
    closed = "closed"
    flooded = "flooded"
    potholed = "potholed"
    gravel = "gravel"


class RoadState(BaseModel):
    """Current state of a single OSM/SUMO road edge."""

    schema_version: str = Field("1.0", description="Schema version string")
    edge_id: str = Field(..., description="OSM/SUMO edge identifier")
    timestamp_utc: datetime = Field(..., description="UTC timestamp of this state snapshot")
    lanes_available: int = Field(..., ge=0, description="Number of lanes currently usable")
    total_lanes: int = Field(..., ge=1, description="Total number of lanes on the edge")
    speed_limit_mps: float = Field(..., ge=0.0, description="Posted speed limit in m/s")
    road_condition: RoadCondition = Field(
        RoadCondition.clear, description="Current surface condition"
    )
    source: str = Field(..., description="Data origin identifier")
    scenario_run_id: Optional[str] = Field(None, description="Simulation scenario run identifier")


class RoadEvent(BaseModel):
    """A discrete event affecting a road edge (closure, hazard, construction, etc.)."""

    schema_version: str = Field("1.0", description="Schema version string")
    event_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Unique event identifier (UUID)",
    )
    edge_id: str = Field(..., description="OSM/SUMO edge identifier affected by this event")
    event_type: str = Field(
        ...,
        description=(
            "Event category: 'closure', 'narrowing', 'construction', 'hazard', 'animal_crossing'"
        ),
    )
    start_time_utc: datetime = Field(..., description="UTC time when event begins")
    end_time_utc: Optional[datetime] = Field(None, description="UTC time when event ends (if known)")
    severity: float = Field(..., description="Event severity in [0, 1]")
    description: str = Field(..., description="Human-readable event description")
    source: str = Field(..., description="Data origin identifier")

    @field_validator("severity")
    @classmethod
    def severity_in_range(cls, v: float) -> float:
        if not (0.0 <= v <= 1.0):
            raise ValueError(f"severity must be in [0, 1], got {v}")
        return v
