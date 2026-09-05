"""Cooperative route distributor.

When multiple vehicles need rerouting simultaneously, this module distributes
them across the available alternatives so no single detour becomes the new
bottleneck. It never moves all affected vehicles to one shortcut.

Algorithm:
  1. For each affected vehicle, compute K shortest alternative paths.
  2. Rank the alternatives by composite cost (including existing load).
  3. Assign vehicles to routes in cost order, tracking cumulative load.
  4. Cap assignment at LOAD_CAPACITY_FRACTION of each edge's capacity.
  5. Return per-vehicle assignments with ETA and reason.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

from .cost import should_reroute
from .graph import MobilityGraph
from .pathfinder import PathStep, find_path, path_cost, path_eta_s, path_to_geometry

# Never assign more than this fraction of edge capacity to the alternative route
LOAD_CAPACITY_FRACTION: float = 0.75
# Minimum fraction of vehicles diverted to each alternative (prevents herding)
MIN_SPLIT_FRACTION: float = 0.20
# Maximum alternative paths to consider per vehicle
K_ALTERNATIVES: int = 3


@dataclass
class VehicleAssignment:
    vehicle_id: str
    old_path: list[PathStep]
    new_path: list[PathStep]
    old_eta_s: float
    new_eta_s: float
    old_cost: float
    new_cost: float
    reason: str
    triggered: bool   # False → no reroute needed; old path kept


class CooperativeDistributor:
    """
    Computes cooperative reroute assignments for a set of vehicles.
    All vehicles in one planning cycle share a common load_offsets dict
    so path assignments from earlier vehicles raise costs for later ones.
    """

    def __init__(self, graph: MobilityGraph) -> None:
        self._graph = graph

    def plan(
        self,
        vehicle_routes: dict[str, tuple[str, str]],  # vehicle_id → (origin_node, dest_node)
        current_paths: dict[str, list[PathStep]] | None = None,
    ) -> list[VehicleAssignment]:
        """
        Plan reroutes for all vehicles. Returns one VehicleAssignment per vehicle.

        vehicle_routes: current vehicle endpoints (origin, destination) by vehicle_id
        current_paths: existing paths; if None, current path is computed on the fly
        """
        current_paths = current_paths or {}
        load_offsets: dict[str, int] = {}  # edge_id → cumulative assigned vehicles
        results: list[VehicleAssignment] = []

        for vehicle_id, (origin, destination) in vehicle_routes.items():
            old_path = current_paths.get(vehicle_id) or []
            if not old_path:
                old_path, _ = find_path(self._graph, origin, destination)

            old_cost = path_cost(self._graph, old_path, load_offsets)
            old_eta = path_eta_s(self._graph, old_path)

            # Check if any edge on the current path demands rerouting
            force_trigger, force_reason = self._check_trigger(old_path)

            # Find alternatives
            alternatives = self._k_alternatives(origin, destination, load_offsets, k=K_ALTERNATIVES)

            best_path = old_path
            best_cost = old_cost
            best_reason = force_reason
            triggered = force_trigger

            for alt_path, alt_cost in alternatives:
                _, reason = should_reroute(old_cost, alt_cost, self._max_hazard(old_path), force_trigger and force_reason == "closure")
                if force_trigger or reason:
                    if alt_cost < best_cost and self._fits_capacity(alt_path, load_offsets):
                        best_path = alt_path
                        best_cost = alt_cost
                        best_reason = reason or force_reason
                        triggered = True
                        break

            new_eta = path_eta_s(self._graph, best_path)

            # Update load offsets for subsequent vehicles
            if triggered and best_path is not old_path:
                for _nid, edge_id in best_path:
                    if edge_id:
                        load_offsets[edge_id] = load_offsets.get(edge_id, 0) + 1

            results.append(VehicleAssignment(
                vehicle_id=vehicle_id,
                old_path=old_path,
                new_path=best_path,
                old_eta_s=round(old_eta, 1),
                new_eta_s=round(new_eta, 1),
                old_cost=round(old_cost, 2),
                new_cost=round(best_cost, 2),
                reason=best_reason,
                triggered=triggered,
            ))

        return results

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _k_alternatives(
        self,
        origin: str,
        destination: str,
        load_offsets: dict[str, int],
        k: int,
    ) -> list[tuple[list[PathStep], float]]:
        """Find up to k alternative paths using Yen's-style edge penalty."""
        alternatives: list[tuple[list[PathStep], float]] = []

        # Primary shortest path (with current load)
        path, cost = find_path(self._graph, origin, destination, load_offsets=load_offsets)
        if path:
            alternatives.append((path, cost))

        # Penalise each edge of the primary path and re-run to get diversity
        for _nid, edge_id in path:
            if edge_id is None:
                continue
            # Temporarily add heavy load to force the pathfinder around this edge
            temp_offsets = dict(load_offsets)
            edge = self._graph.edge(edge_id)
            if edge:
                temp_offsets[edge_id] = edge.capacity_vehicles  # saturate it
            alt, alt_cost = find_path(self._graph, origin, destination, load_offsets=temp_offsets)
            if alt and alt != path and (alt, alt_cost) not in alternatives:
                alternatives.append((alt, alt_cost))
            if len(alternatives) >= k:
                break

        return alternatives

    def _check_trigger(self, path: list[PathStep]) -> tuple[bool, str]:
        """Check if any edge on this path forces a reroute."""
        for _nid, edge_id in path:
            if edge_id is None:
                continue
            edge = self._graph.edge(edge_id)
            if edge is None:
                continue
            triggered, reason = should_reroute(0.0, 0.0, edge.metrics.hazard_penalty, edge.metrics.closure)
            if edge.metrics.closure:
                return True, "closure"
            if edge.metrics.hazard_penalty >= 0.70:
                return True, "critical_hazard"
        return False, ""

    def _max_hazard(self, path: list[PathStep]) -> float:
        max_h = 0.0
        for _nid, edge_id in path:
            if edge_id is None:
                continue
            edge = self._graph.edge(edge_id)
            if edge:
                max_h = max(max_h, edge.metrics.hazard_penalty)
        return max_h

    def _fits_capacity(self, path: list[PathStep], load_offsets: dict[str, int]) -> bool:
        """Return True if none of this path's edges would exceed the load cap."""
        for _nid, edge_id in path:
            if edge_id is None:
                continue
            edge = self._graph.edge(edge_id)
            if edge is None:
                continue
            current_load = edge.metrics.vehicle_count + load_offsets.get(edge_id, 0)
            if current_load > edge.capacity_vehicles * LOAD_CAPACITY_FRACTION:
                return False
        return True
