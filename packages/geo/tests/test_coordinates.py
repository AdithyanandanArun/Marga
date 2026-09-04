from dataclasses import dataclass

import pytest

from packages.geo import (
    LocalTangentPlane,
    angular_difference_deg,
    bearing_deg,
    distance_m,
    normalize_heading_deg,
)


@dataclass
class Point:
    lat: float
    lon: float


def test_haversine_distance_is_accurate_at_100_metres() -> None:
    origin = Point(12.9716, 77.5946)
    north_100m = Point(12.9724993204, 77.5946)
    assert distance_m(origin, north_100m) == pytest.approx(100.0, abs=0.2)


def test_heading_wrap_is_stable() -> None:
    assert normalize_heading_deg(360) == 0
    assert normalize_heading_deg(-1) == 359
    assert angular_difference_deg(359, 1) == 2
    assert angular_difference_deg(1, 359) == -2


def test_local_projection_round_trips() -> None:
    plane = LocalTangentPlane(12.9716, 77.5946)
    east, north = plane.project(Point(12.9720, 77.5950))
    lat, lon = plane.unproject(east, north)
    assert lat == pytest.approx(12.9720)
    assert lon == pytest.approx(77.5950)
    assert bearing_deg(Point(0, 0), Point(1, 0)) == pytest.approx(0)
