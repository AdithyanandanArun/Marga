"""Hazard observation schema for detected road hazards."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator

from .common import PositionEstimate


class HazardObservation(BaseModel):
    """An observed or inferred hazard on or near the road network."""

    schema_version: str = Field("1.0", description="Schema version string")
    hazard_id: str = Field(..., description="Unique hazard identifier")
    timestamp_utc: datetime = Field(..., description="UTC timestamp of observation")
    hazard_type: str = Field(
        ...,
        description=(
            "Hazard category: 'pothole', 'debris', 'flooding', 'animal', "
            "'stalled_vehicle', 'wrong_way', 'emergency_vehicle'"
        ),
    )
    road_segment_id: str | None = Field(
        None,
        description="Canonical OSM/SUMO edge ID when the observation is map matched",
    )
    position: PositionEstimate = Field(..., description="Estimated hazard position")
    confidence: float = Field(..., description="Detection confidence in [0, 1]")
    expires_at: Optional[datetime] = Field(
        None, description="UTC time after which the hazard should be considered stale"
    )
    reporting_source: str = Field(
        ..., description="Primary source that reported this hazard"
    )
    corroborating_sources: list[str] = Field(
        default_factory=list,
        description="Additional sources that confirmed this hazard",
    )
    evidence: dict[str, Any] = Field(
        default_factory=dict,
        description="Arbitrary evidence payload (images, sensor readings, etc.)",
    )

    @field_validator("confidence")
    @classmethod
    def confidence_in_range(cls, v: float) -> float:
        if not (0.0 <= v <= 1.0):
            raise ValueError(f"confidence must be in [0, 1], got {v}")
        return v
