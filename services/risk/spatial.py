"""Dependency-light spatial candidate index for the risk hot path."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from math import floor

from packages.geo import LocalTangentPlane
from packages.schemas import PositionEstimate


class UniformGridIndex:
    """Local metric grid that narrows risk candidates without changing semantics.

    It is intentionally a replaceable seam for H3/R-tree/PostGIS. Candidate
    discovery is separate from risk scoring, enabling profiling and parity
    tests against the exhaustive implementation.
    """

    def __init__(
        self, estimates: Iterable[PositionEstimate], *, cell_size_m: float = 100.0
    ) -> None:
        if cell_size_m <= 0:
            raise ValueError("cell_size_m must be positive")
        self.cell_size_m = cell_size_m
        self.estimates = tuple(estimates)
        self._cells: dict[tuple[int, int], list[PositionEstimate]] = {}
        self._coordinates: dict[str, tuple[float, float]] = {}
        if not self.estimates:
            self._plane = None
            return
        origin = self.estimates[0].position
        self._plane = LocalTangentPlane(origin.lat, origin.lon)
        self._cells = defaultdict(list)
        self._coordinates = {}
        for estimate in self.estimates:
            east, north = self._plane.project(estimate.position)
            self._coordinates[str(estimate.estimate_id)] = (east, north)
            self._cells[self._cell(east, north)].append(estimate)

    def _cell(self, east_m: float, north_m: float) -> tuple[int, int]:
        return floor(east_m / self.cell_size_m), floor(north_m / self.cell_size_m)

    def nearby(self, estimate: PositionEstimate, *, radius_m: float) -> list[PositionEstimate]:
        if radius_m < 0:
            raise ValueError("radius_m must be non-negative")
        if self._plane is None:
            return []
        east, north = self._coordinates[str(estimate.estimate_id)]
        center_x, center_y = self._cell(east, north)
        cells = max(0, int(radius_m / self.cell_size_m) + 1)
        candidates: list[PositionEstimate] = []
        radius_squared = radius_m**2
        for x in range(center_x - cells, center_x + cells + 1):
            for y in range(center_y - cells, center_y + cells + 1):
                for candidate in self._cells.get((x, y), []):
                    candidate_east, candidate_north = self._coordinates[str(candidate.estimate_id)]
                    if (candidate_east - east) ** 2 + (
                        candidate_north - north
                    ) ** 2 <= radius_squared:
                        candidates.append(candidate)
        return candidates
