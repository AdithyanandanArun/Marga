"""Geospatial utilities and WGS84 geometry helpers for Marga V2X platform."""

from packages.geo.helpers import (
    bearing_between,
    bearing_difference,
    haversine_distance,
    heading_alignment,
    normalize_heading,
    point_along_bearing,
    project_position,
)
from .coordinates import (
    EARTH_RADIUS_M,
    LocalTangentPlane,
    angular_difference_deg,
    bearing_deg,
    distance_m,
    normalize_heading_deg,
)

__all__ = [
    "EARTH_RADIUS_M",
    "LocalTangentPlane",
    "angular_difference_deg",
    "bearing_between",
    "bearing_deg",
    "bearing_difference",
    "distance_m",
    "haversine_distance",
    "heading_alignment",
    "normalize_heading",
    "normalize_heading_deg",
    "point_along_bearing",
    "project_position",
]
