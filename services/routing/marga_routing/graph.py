"""Mobility graph data model.

Mirrors GraphEdgeMetrics from the frontend contract (types/graph.ts).
Edges carry both static geometry and live metrics pushed by Adithyan1.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Node:
    node_id: str
    lat: float
    lon: float
    label: str = ""


@dataclass
class EdgeMetrics:
    """Live metrics from Adithyan1's mobility graph — updated on every tick."""

    avg_speed_mps: float = 8.0
    vehicle_count: int = 0
    queue_length: int = 0
    capacity_ratio: float = 0.0
    hazard_penalty: float = 0.0
    gps_confidence: float = 1.0
    downstream_congestion: float = 0.0
    two_wheeler_ratio: float = 0.0
    flow_rate_vph: float = 0.0
    occupancy: float = 0.0
    closure: bool = False


@dataclass
class Edge:
    edge_id: str
    src: str                     # node_id
    dst: str                     # node_id
    length_m: float
    speed_limit_mps: float = 11.0  # ~40 km/h urban India default
    lane_count: int = 2
    capacity_vehicles: int = 40
    metrics: EdgeMetrics = field(default_factory=EdgeMetrics)

    def travel_time_s(self) -> float:
        speed = max(0.5, self.metrics.avg_speed_mps)
        return self.length_m / speed


class MobilityGraph:
    """
    Directed graph of road segments. Edges are updated live via ingest_metrics().
    Used by the pathfinder to compute cost-weighted shortest paths.
    """

    def __init__(self) -> None:
        self._nodes: dict[str, Node] = {}
        self._edges: dict[str, Edge] = {}
        self._adjacency: dict[str, list[str]] = {}  # src_node_id → [edge_id, ...]

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def add_node(self, node: Node) -> None:
        self._nodes[node.node_id] = node
        if node.node_id not in self._adjacency:
            self._adjacency[node.node_id] = []

    def add_edge(self, edge: Edge) -> None:
        self._edges[edge.edge_id] = edge
        self._adjacency.setdefault(edge.src, []).append(edge.edge_id)

    def ingest_metrics(self, edge_id: str, metrics: EdgeMetrics) -> None:
        if edge_id in self._edges:
            self._edges[edge_id].metrics = metrics

    def ingest_metrics_dict(self, edge_id: str, data: dict) -> None:
        em = EdgeMetrics(
            avg_speed_mps=float(data.get("avg_speed_mps", 8.0)),
            vehicle_count=int(data.get("vehicle_count", 0)),
            queue_length=int(data.get("queue_length", 0)),
            capacity_ratio=float(data.get("capacity_ratio", 0.0)),
            hazard_penalty=float(data.get("hazard_penalty", 0.0)),
            gps_confidence=float(data.get("gps_confidence", 1.0)),
            downstream_congestion=float(data.get("downstream_congestion", 0.0)),
            two_wheeler_ratio=float(data.get("two_wheeler_ratio", 0.0)),
            flow_rate_vph=float(data.get("flow_rate_vph", 0.0)),
            occupancy=float(data.get("occupancy", 0.0)),
            closure=bool(data.get("closure", False)),
        )
        self.ingest_metrics(edge_id, em)

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def node(self, node_id: str) -> Optional[Node]:
        return self._nodes.get(node_id)

    def edge(self, edge_id: str) -> Optional[Edge]:
        return self._edges.get(edge_id)

    def edges_from(self, node_id: str) -> list[Edge]:
        return [self._edges[eid] for eid in self._adjacency.get(node_id, []) if eid in self._edges]

    def all_nodes(self) -> list[Node]:
        return list(self._nodes.values())

    def all_edges(self) -> list[Edge]:
        return list(self._edges.values())

    def node_count(self) -> int:
        return len(self._nodes)

    def edge_count(self) -> int:
        return len(self._edges)

    # ------------------------------------------------------------------
    # Geometry helpers
    # ------------------------------------------------------------------

    @staticmethod
    def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        R = 6_371_000.0
        φ1, φ2 = math.radians(lat1), math.radians(lat2)
        dφ = math.radians(lat2 - lat1)
        dλ = math.radians(lon2 - lon1)
        a = math.sin(dφ / 2) ** 2 + math.cos(φ1) * math.cos(φ2) * math.sin(dλ / 2) ** 2
        return R * 2 * math.asin(math.sqrt(a))

    def node_distance_m(self, a: str, b: str) -> float:
        na, nb = self._nodes.get(a), self._nodes.get(b)
        if na is None or nb is None:
            return float("inf")
        return self.haversine_m(na.lat, na.lon, nb.lat, nb.lon)
