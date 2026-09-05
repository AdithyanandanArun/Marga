"""A* pathfinder over MobilityGraph.

Uses edge_cost() for weighted shortest path. Falls back to Dijkstra
(A* with h=0) when no heuristic is available or the graph is not
geo-referenced.

Returns a list of (node_id, edge_id) pairs — the first step has
edge_id=None (the origin node itself).
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass, field
from typing import Optional

from .cost import edge_cost
from .graph import MobilityGraph


@dataclass(order=True)
class _HeapItem:
    f: float
    g: float = field(compare=False)
    node_id: str = field(compare=False)
    edge_id: Optional[str] = field(compare=False, default=None)
    parent: Optional["_HeapItem"] = field(compare=False, default=None)


PathStep = tuple[str, Optional[str]]  # (node_id, edge_id_used_to_arrive | None)


def find_path(
    graph: MobilityGraph,
    origin: str,
    destination: str,
    *,
    load_offsets: dict[str, int] | None = None,
    use_heuristic: bool = True,
) -> tuple[list[PathStep], float]:
    """
    A* from origin to destination.

    Returns (path, total_cost).  path is empty if no route exists.
    load_offsets maps edge_id → extra vehicle count added by the cooperative
    distributor for this planning cycle so already-assigned edges appear costlier.
    """
    load_offsets = load_offsets or {}

    if origin == destination:
        return [(origin, None)], 0.0

    if graph.node(origin) is None or graph.node(destination) is None:
        return [], float("inf")

    def h(node_id: str) -> float:
        if not use_heuristic:
            return 0.0
        # Admissible heuristic: straight-line distance at 13.9 m/s (50 km/h)
        d = graph.node_distance_m(node_id, destination)
        return d / 13.9

    open_heap: list[_HeapItem] = []
    best_g: dict[str, float] = {}
    closed: set[str] = set()

    start = _HeapItem(f=h(origin), g=0.0, node_id=origin)
    heapq.heappush(open_heap, start)
    best_g[origin] = 0.0

    while open_heap:
        current = heapq.heappop(open_heap)

        if current.node_id in closed:
            continue
        closed.add(current.node_id)

        if current.node_id == destination:
            # Reconstruct path
            path: list[PathStep] = []
            item: Optional[_HeapItem] = current
            while item is not None:
                path.append((item.node_id, item.edge_id))
                item = item.parent
            path.reverse()
            return path, current.g

        for edge in graph.edges_from(current.node_id):
            if edge.dst in closed:
                continue
            load = load_offsets.get(edge.edge_id, 0)
            step_cost = edge_cost(edge, load_offset=load)
            new_g = current.g + step_cost

            if new_g < best_g.get(edge.dst, float("inf")):
                best_g[edge.dst] = new_g
                f = new_g + h(edge.dst)
                heapq.heappush(
                    open_heap,
                    _HeapItem(f=f, g=new_g, node_id=edge.dst, edge_id=edge.edge_id, parent=current),
                )

    return [], float("inf")


def path_cost(graph: MobilityGraph, path: list[PathStep], load_offsets: dict[str, int] | None = None) -> float:
    """Compute total cost of a pre-computed path (for comparison after metrics update)."""
    load_offsets = load_offsets or {}
    total = 0.0
    for _node_id, edge_id in path:
        if edge_id is None:
            continue
        edge = graph.edge(edge_id)
        if edge is None:
            return float("inf")
        total += edge_cost(edge, load_offset=load_offsets.get(edge_id, 0))
    return total


def path_to_geometry(graph: MobilityGraph, path: list[PathStep]) -> list[dict]:
    """Convert a path to a list of {lat, lon} waypoints."""
    waypoints = []
    for node_id, _edge_id in path:
        node = graph.node(node_id)
        if node:
            waypoints.append({"lat": node.lat, "lon": node.lon})
    return waypoints


def path_eta_s(graph: MobilityGraph, path: list[PathStep]) -> float:
    """Estimate travel time (seconds) along path using current edge speeds."""
    total = 0.0
    for _node_id, edge_id in path:
        if edge_id is None:
            continue
        edge = graph.edge(edge_id)
        if edge:
            total += edge.travel_time_s()
    return total
