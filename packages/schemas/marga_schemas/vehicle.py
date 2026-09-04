"""Canonical VehicleState schema."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from marga_schemas.common import ActorType, GeoPoint, SchemaVersioned, Source


class VehicleState(SchemaVersioned):
    actor_id: str
    actor_type: ActorType = ActorType.CAR
    ts: datetime
    position: GeoPoint
    position_uncertainty_m: float = Field(ge=0)
    speed_mps: float = Field(ge=0)
    acceleration_mps2: float | None = None
    heading_deg: float = Field(ge=0, lt=360)
    yaw_rate_dps: float | None = None
    road_segment_id: str | None = None
    lane_id: str | None = None
    source: Source
    trust_context_id: str | None = None
    capabilities: list[str] = Field(default_factory=list)
