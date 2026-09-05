"""Canonical contracts for Marga's live mobility graph.

The graph is a projection of canonical actor, road, hazard, and signal state.
It deliberately contains no SUMO, OSM-client, or frontend-specific types.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from .canonical import SCHEMA_VERSION


class GraphEdgeDefinition(BaseModel):
    """Static topology/capacity metadata supplied by an OSM or SUMO adapter."""

    schema_version: str = SCHEMA_VERSION
    edge_id: str
    intersection_id: str | None = None
    lane_count: int = Field(ge=1, default=1)
    length_m: float | None = Field(ge=0, default=None)
    capacity_vehicles: int = Field(ge=1, default=20)
    downstream_edge_ids: list[str] = Field(default_factory=list)
    source: str


class RollingEdgeMetrics(BaseModel):
    """Aggregate edge measurements for one trailing time window."""

    window_s: int = Field(gt=0)
    sample_count: int = Field(ge=0)
    avg_vehicle_count: float = Field(ge=0)
    avg_speed_mps: float = Field(ge=0)
    avg_queue_length: float = Field(ge=0)
    avg_occupancy: float = Field(ge=0)
    flow_rate_vph: float = Field(ge=0)


class MobilityEdgeState(BaseModel):
    """Confidence-aware, live condition of an OSM/SUMO road edge."""

    schema_version: str = SCHEMA_VERSION
    edge_id: str
    ts: datetime
    intersection_id: str | None = None
    lane_count: int = Field(ge=1)
    capacity_vehicles: int = Field(ge=1)
    vehicle_count: int = Field(ge=0)
    pedestrian_count: int = Field(ge=0)
    density: float = Field(ge=0)
    two_wheeler_ratio: float = Field(ge=0, le=1)
    avg_speed_mps: float = Field(ge=0)
    queue_length: int = Field(ge=0)
    flow_rate_vph: float = Field(ge=0)
    occupancy: float = Field(ge=0)
    capacity_ratio: float = Field(ge=0)
    hazard_penalty: float = Field(ge=0, le=1)
    gps_confidence: float = Field(ge=0, le=1)
    downstream_congestion: float = Field(ge=0)
    rolling_windows: dict[str, RollingEdgeMetrics] = Field(default_factory=dict)
    confidence: float = Field(ge=0, le=1)
    evidence: list[dict[str, object]] = Field(default_factory=list)
    provenance: list[str] = Field(default_factory=list)


class MobilityIntersectionState(BaseModel):
    """Aggregate mobility condition for all edges approaching an intersection."""

    schema_version: str = SCHEMA_VERSION
    intersection_id: str
    ts: datetime
    edge_ids: list[str] = Field(default_factory=list)
    vehicle_count: int = Field(ge=0)
    pedestrian_count: int = Field(ge=0)
    avg_speed_mps: float = Field(ge=0)
    queue_length: int = Field(ge=0)
    occupancy: float = Field(ge=0)
    downstream_congestion: float = Field(ge=0)
    gps_confidence: float = Field(ge=0, le=1)
    confidence: float = Field(ge=0, le=1)
    evidence: list[dict[str, object]] = Field(default_factory=list)
    provenance: list[str] = Field(default_factory=list)
