"""Canonical Hazard and HazardObservation schemas."""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from marga_schemas.common import GeoPoint, SchemaVersioned
from pydantic import Field


class HazardType(str, enum.Enum):
    POTHOLE = "POTHOLE"
    BUMP = "BUMP"
    DEBRIS = "DEBRIS"
    FLOOD = "FLOOD"
    LANDSLIDE = "LANDSLIDE"
    ANIMAL = "ANIMAL"
    STALLED_VEHICLE = "STALLED_VEHICLE"
    CONSTRUCTION = "CONSTRUCTION"
    LANE_CLOSURE = "LANE_CLOSURE"
    ACCIDENT = "ACCIDENT"
    LOW_VISIBILITY = "LOW_VISIBILITY"
    OTHER = "OTHER"


class HazardState(str, enum.Enum):
    CANDIDATE = "CANDIDATE"
    VERIFIED = "VERIFIED"
    STALE = "STALE"
    EXPIRED = "EXPIRED"


class HazardObservation(SchemaVersioned):
    observation_id: UUID = Field(default_factory=uuid4)
    hazard_type: HazardType
    position: GeoPoint
    observed_at: datetime
    source_id: str
    detector_confidence: float = Field(ge=0, le=1)
    severity_hint: float = Field(ge=0, le=1, default=0.5)
    road_segment_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class Hazard(SchemaVersioned):
    hazard_id: UUID = Field(default_factory=uuid4)
    hazard_type: HazardType
    position: GeoPoint
    geometry: dict[str, Any] | None = None
    severity: float = Field(ge=0, le=1)
    confidence: float = Field(ge=0, le=1)
    first_seen: datetime
    last_seen: datetime
    ttl_s: int = Field(ge=0)
    source_ids: list[str] = Field(default_factory=list)
    evidence_count: int = Field(ge=0, default=0)
    state: HazardState = HazardState.CANDIDATE
    road_segment_id: str | None = None
    contradiction_count: int = Field(ge=0, default=0)
