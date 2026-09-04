"""Unit-tested geospatial helper functions.

Handles projection, heading normalization, distances, and position
prediction for the Marga V2X platform.
"""

from __future__ import annotations

import math

EARTH_RADIUS_M = 6_371_000.0


def normalize_heading(deg: float) -> float:
    """Normalize heading to [0, 360)."""
    return deg % 360


def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Compute great-circle distance in meters between two WGS84 points."""
    rlat1, rlon1 = math.radians(lat1), math.radians(lon1)
    rlat2, rlon2 = math.radians(lat2), math.radians(lon2)
    dlat = rlat2 - rlat1
    dlon = rlon2 - rlon1
    a = math.sin(dlat / 2) ** 2 + math.cos(rlat1) * math.cos(rlat2) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


def bearing_between(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Compute initial bearing (degrees, clockwise from north) from point 1 to point 2."""
    rlat1, rlon1 = math.radians(lat1), math.radians(lon1)
    rlat2, rlon2 = math.radians(lat2), math.radians(lon2)
    dlon = rlon2 - rlon1
    x = math.sin(dlon) * math.cos(rlat2)
    y = math.cos(rlat1) * math.sin(rlat2) - math.sin(rlat1) * math.cos(rlat2) * math.cos(dlon)
    return normalize_heading(math.degrees(math.atan2(x, y)))


def bearing_difference(a: float, b: float) -> float:
    """Compute signed angular difference between two headings in [-180, 180]."""
    diff = normalize_heading(b) - normalize_heading(a)
    if diff > 180:
        diff -= 360
    elif diff < -180:
        diff += 360
    return diff


def heading_alignment(heading: float, road_direction: float) -> float:
    """Compute alignment score between heading and road direction.

    Returns value in [-1, 1]:
      +1 = perfectly aligned (same direction)
      -1 = perfectly opposing (wrong way)
       0 = perpendicular
    """
    diff = bearing_difference(heading, road_direction)
    return math.cos(math.radians(diff))


def point_along_bearing(lat: float, lon: float, bearing_deg: float, distance_m: float) -> tuple[float, float]:
    """Compute destination point given start, bearing, and distance."""
    rlat = math.radians(lat)
    rlon = math.radians(lon)
    rbearing = math.radians(bearing_deg)
    angular_dist = distance_m / EARTH_RADIUS_M

    new_lat = math.asin(
        math.sin(rlat) * math.cos(angular_dist) + math.cos(rlat) * math.sin(angular_dist) * math.cos(rbearing)
    )
    new_lon = rlon + math.atan2(
        math.sin(rbearing) * math.sin(angular_dist) * math.cos(rlat),
        math.cos(angular_dist) - math.sin(rlat) * math.sin(new_lat),
    )
    return math.degrees(new_lat), math.degrees(new_lon)


def project_position(
    lat: float,
    lon: float,
    heading_deg: float,
    speed_mps: float,
    dt_s: float,
    acceleration_mps2: float = 0.0,
) -> tuple[float, float, float]:
    """Project a position forward by dt seconds along heading with optional acceleration.

    Returns (new_lat, new_lon, new_speed_mps).
    """
    new_speed = max(0.0, speed_mps + acceleration_mps2 * dt_s)
    avg_speed = (speed_mps + new_speed) / 2
    distance = avg_speed * dt_s
    new_lat, new_lon = point_along_bearing(lat, lon, heading_deg, distance)
    return new_lat, new_lon, new_speed
