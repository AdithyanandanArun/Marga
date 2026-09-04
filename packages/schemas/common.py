"""Common building-block types shared across all Marga schemas."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field, field_validator


class Position(BaseModel):
    """Raw geographic position (WGS-84)."""

    lat: float = Field(..., ge=-90.0, le=90.0, description="Latitude in decimal degrees")
    lon: float = Field(..., ge=-180.0, le=180.0, description="Longitude in decimal degrees")
    alt_m: Optional[float] = Field(None, description="Altitude above sea level in metres")


class PositionEstimate(BaseModel):
    """Geographic position enriched with uncertainty and provenance metadata."""

    lat: float = Field(..., ge=-90.0, le=90.0, description="Latitude in decimal degrees")
    lon: float = Field(..., ge=-180.0, le=180.0, description="Longitude in decimal degrees")
    alt_m: Optional[float] = Field(None, description="Altitude above sea level in metres")
    uncertainty_m: float = Field(..., ge=0.0, description="1-sigma position uncertainty in metres")
    confidence: float = Field(..., description="Localisation confidence score in [0, 1]")
    source: str = Field(..., description="Originating system or sensor identifier")

    @field_validator("confidence")
    @classmethod
    def confidence_in_range(cls, v: float) -> float:
        if not (0.0 <= v <= 1.0):
            raise ValueError(f"confidence must be between 0 and 1, got {v}")
        return v


class SourceMetadata(BaseModel):
    """Provenance tag attached to any message produced by a Marga subsystem."""

    system: str = Field(..., description="Top-level system name, e.g. 'sumo_adapter'")
    component: str = Field(..., description="Sub-component within the system")
    schema_version: str = Field("1.0", description="Schema version string")
