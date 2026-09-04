"""WGS84 helpers used at service boundaries and in short-range risk math.

Latitude/longitude stays at the boundary.  ``LocalTangentPlane`` provides a
simple east/north metric frame for the small active areas used by position and
risk services; it is deliberately not a replacement for a projected CRS over
city- or country-scale distances.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import asin, atan2, cos, degrees, isfinite, radians, sin, sqrt
from typing import Protocol

EARTH_RADIUS_M = 6_371_008.8


class HasLatLon(Protocol):
    lat: float
    lon: float


def _validate_lat_lon(lat: float, lon: float) -> None:
    if not (isfinite(lat) and isfinite(lon)):
        raise ValueError("latitude and longitude must be finite")
    if not -90.0 <= lat <= 90.0:
        raise ValueError("latitude must be within [-90, 90]")
    if not -180.0 <= lon <= 180.0:
        raise ValueError("longitude must be within [-180, 180]")


def normalize_heading_deg(heading_deg: float) -> float:
    """Normalize a clockwise-from-true-north heading into ``[0, 360)``."""
    if not isfinite(heading_deg):
        raise ValueError("heading must be finite")
    normalized = heading_deg % 360.0
    return 0.0 if normalized == 0.0 else normalized


def angular_difference_deg(from_heading_deg: float, to_heading_deg: float) -> float:
    """Return the signed shortest turn from ``from`` to ``to`` in ``[-180, 180)``."""
    return (
        normalize_heading_deg(to_heading_deg) - normalize_heading_deg(from_heading_deg) + 180.0
    ) % 360.0 - 180.0


def distance_m(a: HasLatLon, b: HasLatLon) -> float:
    """Great-circle WGS84-interface distance using the haversine formula."""
    _validate_lat_lon(a.lat, a.lon)
    _validate_lat_lon(b.lat, b.lon)
    lat_delta = radians(b.lat - a.lat)
    lon_delta = radians(b.lon - a.lon)
    lat_a = radians(a.lat)
    lat_b = radians(b.lat)
    h = sin(lat_delta / 2.0) ** 2 + cos(lat_a) * cos(lat_b) * sin(lon_delta / 2.0) ** 2
    return 2.0 * EARTH_RADIUS_M * asin(min(1.0, sqrt(h)))


def bearing_deg(a: HasLatLon, b: HasLatLon) -> float:
    """Initial true-north bearing from ``a`` to ``b`` in ``[0, 360)``."""
    _validate_lat_lon(a.lat, a.lon)
    _validate_lat_lon(b.lat, b.lon)
    lat_a, lat_b = radians(a.lat), radians(b.lat)
    delta_lon = radians(b.lon - a.lon)
    east = sin(delta_lon) * cos(lat_b)
    north = cos(lat_a) * sin(lat_b) - sin(lat_a) * cos(lat_b) * cos(delta_lon)
    if east == 0.0 and north == 0.0:
        raise ValueError("bearing is undefined for coincident points")
    return normalize_heading_deg(degrees(atan2(east, north)))


@dataclass(frozen=True, slots=True)
class LocalTangentPlane:
    """Equirectangular local east/north projection centred on an origin.

    Accurate enough for local safety calculations (roughly a few kilometres
    around the origin); use a proper projected CRS for large map operations.
    """

    origin_lat: float
    origin_lon: float

    def __post_init__(self) -> None:
        _validate_lat_lon(self.origin_lat, self.origin_lon)

    def project(self, point: HasLatLon) -> tuple[float, float]:
        """Return ``(east_m, north_m)`` relative to the origin."""
        _validate_lat_lon(point.lat, point.lon)
        north_m = radians(point.lat - self.origin_lat) * EARTH_RADIUS_M
        east_m = (
            radians(point.lon - self.origin_lon) * EARTH_RADIUS_M * cos(radians(self.origin_lat))
        )
        return east_m, north_m

    def unproject(self, east_m: float, north_m: float) -> tuple[float, float]:
        """Return ``(lat, lon)`` for a local metric coordinate."""
        if not (isfinite(east_m) and isfinite(north_m)):
            raise ValueError("local coordinates must be finite")
        latitude = self.origin_lat + degrees(north_m / EARTH_RADIUS_M)
        cos_lat = cos(radians(self.origin_lat))
        if abs(cos_lat) < 1e-12:
            raise ValueError("local tangent projection is undefined at the poles")
        longitude = self.origin_lon + degrees(east_m / (EARTH_RADIUS_M * cos_lat))
        _validate_lat_lon(latitude, longitude)
        return latitude, longitude
