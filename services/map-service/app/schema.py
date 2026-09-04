"""Canonical schema types (inlined — no external marga package dependency yet)."""

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel


class Position(BaseModel):
    lat: float
    lon: float
    alt_m: Optional[float] = None


class RoadCondition(str, Enum):
    clear = "clear"
    wet = "wet"
    construction = "construction"
    closed = "closed"
    flooded = "flooded"


class RoadEdge(BaseModel):
    edge_id: str
    osm_way_id: Optional[str] = None
    from_node: str
    to_node: str
    length_m: float
    lanes: int
    speed_limit_mps: float
    road_type: str
    name: Optional[str] = None
    geometry: list[Position]


class RoadNode(BaseModel):
    node_id: str
    position: Position


class TrafficSignal(BaseModel):
    signal_id: str
    node_id: str
    position: Position
    controlled_edges: list[str]


class PedestrianCrossing(BaseModel):
    crossing_id: str
    position: Position
    edge_id: Optional[str] = None


class RoadNetwork(BaseModel):
    schema_version: str = "1.0"
    region_name: str
    bbox: dict
    imported_at: datetime
    edges: list[RoadEdge]
    nodes: list[RoadNode]
    signals: list[TrafficSignal]
    crossings: list[PedestrianCrossing]


class ImportReport(BaseModel):
    schema_version: str = "1.0"
    region_name: str
    bbox: dict
    imported_at: datetime
    edge_count: int
    node_count: int
    signal_count: int
    crossing_count: int
    warnings: list[str]
    osm_file_path: str
    net_file_path: str
    duration_s: float
