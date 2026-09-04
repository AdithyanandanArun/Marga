"""Common canonical types shared across all Marga services."""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class SchemaVersioned(BaseModel):
    schema_version: str = "0.1.0"


class GeoPoint(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    altitude_m: float | None = None


class Source(enum.StrEnum):
    SIMULATION = "SIMULATION"
    PHONE = "PHONE"
    OBU = "OBU"
    RSU = "RSU"
    VEHICLE_API = "VEHICLE_API"
    CAMERA = "CAMERA"
    AUTHORITY = "AUTHORITY"
    OPERATOR = "OPERATOR"


class ActorType(enum.StrEnum):
    CAR = "CAR"
    BIKE = "BIKE"
    AUTO = "AUTO"
    BUS = "BUS"
    TRUCK = "TRUCK"
    AMBULANCE = "AMBULANCE"
    PEDESTRIAN = "PEDESTRIAN"
    ANIMAL = "ANIMAL"
    OTHER = "OTHER"


class PositionMethod(enum.StrEnum):
    GNSS = "GNSS"
    FUSED = "FUSED"
    DEAD_RECKONED = "DEAD_RECKONED"
    MAP_MATCHED = "MAP_MATCHED"
    PEER_AIDED = "PEER_AIDED"


class ConnectivityState(enum.StrEnum):
    FULL = "FULL"
    DIRECT_ONLY = "DIRECT_ONLY"
    INTERMITTENT = "INTERMITTENT"
    ISOLATED = "ISOLATED"


class EvidenceItem(BaseModel):
    source_id: str
    source_type: Source
    timestamp: datetime
    confidence: float = Field(ge=0, le=1)
    detail: dict[str, Any] = Field(default_factory=dict)


class PositionEstimate(BaseModel):
    actor_id: str
    ts: datetime
    lat: float
    lon: float
    covariance_2d: list[list[float]] | None = None
    uncertainty_radius_m: float
    confidence: float = Field(ge=0, le=1)
    method: PositionMethod
    evidence: list[EvidenceItem] = Field(default_factory=list)
