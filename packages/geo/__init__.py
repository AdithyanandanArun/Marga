"""Small, deterministic WGS84 and local-metric geometry helpers."""

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
    "bearing_deg",
    "distance_m",
    "normalize_heading_deg",
]
