"""Spatial indexing for fast hazard neighbor queries.

Uses a grid-cell approach (bucketed by rounded lat/lon) for O(1) average
neighbor lookup, backed by haversine distance for accurate filtering.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from marga_schemas.hazard import Hazard, HazardType

from marga_schemas.common import GeoPoint

# Earth radius in metres (WGS-84 mean)
_EARTH_RADIUS_M = 6_371_000.0


def haversine_m(a: GeoPoint, b: GeoPoint) -> float:
    """Return the great-circle distance in metres between two GeoPoints."""
    lat1, lon1 = math.radians(a.lat), math.radians(a.lon)
    lat2, lon2 = math.radians(b.lat), math.radians(b.lon)
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * _EARTH_RADIUS_M * math.asin(math.sqrt(h))


def _grid_key(lat: float, lon: float, cell_size_deg: float) -> tuple[int, int]:
    """Return the integer grid-cell key for a lat/lon pair."""
    return (int(math.floor(lat / cell_size_deg)), int(math.floor(lon / cell_size_deg)))


class HazardSpatialIndex:
    """Grid-based spatial index for O(1) average-case neighbor lookup.

    The grid uses a configurable cell size (default ~100 m at the equator).
    Queries fan out to neighboring cells to avoid boundary misses.
    """

    # ~100 m at the equator
    DEFAULT_CELL_SIZE_DEG = 0.001

    def __init__(self, cell_size_deg: float = DEFAULT_CELL_SIZE_DEG) -> None:
        self._cell_size = cell_size_deg
        # grid_key -> {hazard_id: Hazard}
        self._grid: dict[tuple[int, int], dict[str, "Hazard"]] = {}
        # hazard_id -> grid_key (for O(1) removal)
        self._id_to_key: dict[str, tuple[int, int]] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def insert(self, hazard: "Hazard") -> None:
        """Insert or update a hazard in the index."""
        hid = str(hazard.hazard_id)
        # Remove old entry if position changed
        if hid in self._id_to_key:
            self._remove_from_grid(hid)
        key = _grid_key(hazard.position.lat, hazard.position.lon, self._cell_size)
        self._grid.setdefault(key, {})[hid] = hazard
        self._id_to_key[hid] = key

    def remove(self, hazard_id: str) -> None:
        """Remove a hazard from the index by its ID."""
        self._remove_from_grid(hazard_id)

    def query_nearby(
        self,
        position: GeoPoint,
        radius_m: float,
        hazard_type: "HazardType | None" = None,
    ) -> list["Hazard"]:
        """Return all hazards within *radius_m* metres of *position*.

        Optionally filter by hazard type.
        """
        # Compute how many grid cells the radius spans.
        # cell_size in degrees; 1 degree ~ 111 km.  Convert radius to degrees
        # and divide by cell_size to get the cell span.
        radius_deg = radius_m / 111_000.0
        cell_span = max(1, int(math.ceil(radius_deg / self._cell_size)))
        center_key = _grid_key(position.lat, position.lon, self._cell_size)

        results: list["Hazard"] = []
        for di in range(-cell_span, cell_span + 1):
            for dj in range(-cell_span, cell_span + 1):
                cell = self._grid.get((center_key[0] + di, center_key[1] + dj))
                if cell is None:
                    continue
                for hazard in cell.values():
                    if hazard_type is not None and hazard.hazard_type != hazard_type:
                        continue
                    if haversine_m(position, hazard.position) <= radius_m:
                        results.append(hazard)
        return results

    def __len__(self) -> int:
        return len(self._id_to_key)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _remove_from_grid(self, hazard_id: str) -> None:
        key = self._id_to_key.pop(hazard_id, None)
        if key is not None:
            bucket = self._grid.get(key)
            if bucket is not None:
                bucket.pop(hazard_id, None)
                if not bucket:
                    del self._grid[key]
