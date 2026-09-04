"""
Unit tests for SumoNormalizer.

All tests run WITHOUT requiring SUMO to be installed.
They test normalizer logic in isolation with synthetic inputs.
"""

from __future__ import annotations

import math
import pytest
from datetime import datetime, timezone

from services.simulation_adapter.normalizer import SumoNormalizer, DEFAULT_TYPE_MAP
from services.simulation_adapter.schemas import (
    SignalPhase,
    VehicleType,
)


# ---------------------------------------------------------------------------
# Heading normalisation edge cases
# ---------------------------------------------------------------------------

class TestNormalizeHeadingEdgeCases:
    """Edge cases for SumoNormalizer.normalize_heading."""

    def test_zero_sumo_angle_gives_90(self):
        """SUMO 0° (East) → canonical 90° (East)."""
        assert SumoNormalizer.normalize_heading(0.0) == pytest.approx(90.0)

    def test_90_sumo_gives_0(self):
        """SUMO 90° (North) → canonical 0°."""
        assert SumoNormalizer.normalize_heading(90.0) == pytest.approx(0.0)

    def test_180_sumo_gives_270(self):
        """SUMO 180° (West) → canonical 270°."""
        assert SumoNormalizer.normalize_heading(180.0) == pytest.approx(270.0)

    def test_270_sumo_gives_180(self):
        """SUMO 270° (South) → canonical 180°."""
        assert SumoNormalizer.normalize_heading(270.0) == pytest.approx(180.0)

    def test_360_sumo_wraps_to_90(self):
        """SUMO 360° is equivalent to 0° (East) → canonical 90°."""
        assert SumoNormalizer.normalize_heading(360.0) == pytest.approx(90.0)

    def test_negative_one_sumo_angle(self):
        """SUMO -1° is a valid angle; result must be in [0, 360)."""
        result = SumoNormalizer.normalize_heading(-1.0)
        assert 0.0 <= result < 360.0
        assert result == pytest.approx(91.0)

    def test_large_positive_angle_wraps(self):
        """Large positive angle wraps correctly."""
        # SUMO 450° = 450 - 360 = 90° (North) → canonical 0°
        result = SumoNormalizer.normalize_heading(450.0)
        assert result == pytest.approx(0.0)

    def test_large_negative_angle_wraps(self):
        """Large negative angle wraps into [0, 360)."""
        result = SumoNormalizer.normalize_heading(-270.0)
        # (90 - (-270)) % 360 = 360 % 360 = 0
        assert result == pytest.approx(0.0)

    def test_result_always_in_range(self):
        """Canonical heading is always in [0, 360) for any float input."""
        test_angles = [-720, -360, -180, -90, -1, 0, 1, 45, 90, 135, 180, 270, 360, 450, 720]
        for angle in test_angles:
            result = SumoNormalizer.normalize_heading(float(angle))
            assert 0.0 <= result < 360.0, f"Out of range for SUMO {angle}°: got {result}"


# ---------------------------------------------------------------------------
# Speed passthrough tests
# ---------------------------------------------------------------------------

class TestSpeedPassthrough:
    """SUMO speed is already in m/s — must pass through without conversion."""

    def setup_method(self):
        self.normalizer = SumoNormalizer(origin_lat=12.9716, origin_lon=77.5946)
        self.now = datetime.now(timezone.utc)

    def _make_raw(self, speed: float) -> dict:
        return {"x": 0.0, "y": 0.0, "speed": speed, "angle": 90.0, "type_id": "car"}

    def test_zero_speed(self):
        state = self.normalizer.normalize_vehicle_state("v", self._make_raw(0.0), self.now, "r", "s")
        assert state.speed_mps == pytest.approx(0.0)

    def test_walking_speed(self):
        state = self.normalizer.normalize_vehicle_state("v", self._make_raw(1.4), self.now, "r", "s")
        assert state.speed_mps == pytest.approx(1.4)

    def test_city_speed_50kmh(self):
        speed_mps = 50.0 / 3.6
        state = self.normalizer.normalize_vehicle_state("v", self._make_raw(speed_mps), self.now, "r", "s")
        assert state.speed_mps == pytest.approx(speed_mps)

    def test_highway_speed_120kmh(self):
        speed_mps = 120.0 / 3.6
        state = self.normalizer.normalize_vehicle_state("v", self._make_raw(speed_mps), self.now, "r", "s")
        assert state.speed_mps == pytest.approx(speed_mps)

    def test_pedestrian_speed_passthrough(self):
        raw = {"x": 0.0, "y": 0.0, "speed": 1.2, "angle": 0.0}
        state = self.normalizer.normalize_pedestrian_state("p", raw, self.now, "r", "s")
        assert state.speed_mps == pytest.approx(1.2)


# ---------------------------------------------------------------------------
# Coordinate conversion tests
# ---------------------------------------------------------------------------

class TestCoordinateConversion:
    """Test sumo_to_wgs84 with known origin and net offset."""

    def test_origin_maps_to_origin_coords(self):
        """A point at (0, 0) with zero offset maps exactly to origin_lat/lon."""
        normalizer = SumoNormalizer(
            net_offset_x=0.0,
            net_offset_y=0.0,
            origin_lat=12.9716,
            origin_lon=77.5946,
        )
        lat, lon = normalizer.sumo_to_wgs84(0.0, 0.0)
        assert lat == pytest.approx(12.9716, abs=1e-6)
        assert lon == pytest.approx(77.5946, abs=1e-6)

    def test_positive_y_increases_lat(self):
        """Moving North (positive Y in SUMO) increases latitude."""
        normalizer = SumoNormalizer(
            net_offset_x=0.0,
            net_offset_y=0.0,
            origin_lat=12.9716,
            origin_lon=77.5946,
        )
        lat_origin, _ = normalizer.sumo_to_wgs84(0.0, 0.0)
        lat_north, _ = normalizer.sumo_to_wgs84(0.0, 1000.0)
        assert lat_north > lat_origin

    def test_positive_x_increases_lon(self):
        """Moving East (positive X in SUMO) increases longitude."""
        normalizer = SumoNormalizer(
            net_offset_x=0.0,
            net_offset_y=0.0,
            origin_lat=12.9716,
            origin_lon=77.5946,
        )
        _, lon_origin = normalizer.sumo_to_wgs84(0.0, 0.0)
        _, lon_east = normalizer.sumo_to_wgs84(1000.0, 0.0)
        assert lon_east > lon_origin

    def test_1000m_north_approx_0009_deg_lat(self):
        """1000 m north should be approximately +0.009° latitude."""
        normalizer = SumoNormalizer(
            net_offset_x=0.0,
            net_offset_y=0.0,
            origin_lat=0.0,
            origin_lon=0.0,
        )
        lat, _ = normalizer.sumo_to_wgs84(0.0, 1000.0)
        # 1 / 111320 * 1000 ≈ 0.008983
        assert lat == pytest.approx(1000.0 / 111_320.0, rel=1e-3)

    def test_net_offset_applied(self):
        """Net offset shifts the conversion origin correctly."""
        normalizer_no_offset = SumoNormalizer(
            net_offset_x=0.0,
            net_offset_y=0.0,
            origin_lat=12.9716,
            origin_lon=77.5946,
        )
        normalizer_with_offset = SumoNormalizer(
            net_offset_x=500.0,
            net_offset_y=500.0,
            origin_lat=12.9716,
            origin_lon=77.5946,
        )
        # With offset, (0,0) should map to same as no-offset (500, 500)
        lat_no_off, lon_no_off = normalizer_no_offset.sumo_to_wgs84(500.0, 500.0)
        lat_with_off, lon_with_off = normalizer_with_offset.sumo_to_wgs84(0.0, 0.0)
        assert lat_with_off == pytest.approx(lat_no_off, abs=1e-9)
        assert lon_with_off == pytest.approx(lon_no_off, abs=1e-9)

    def test_no_origin_returns_raw_coords(self):
        """Without geo-reference, sumo_to_wgs84 returns (y, x) as fallback."""
        normalizer = SumoNormalizer(
            net_offset_x=0.0,
            net_offset_y=0.0,
            origin_lat=None,
            origin_lon=None,
        )
        lat, lon = normalizer.sumo_to_wgs84(100.0, 200.0)
        # fallback: (y, x)
        assert lat == pytest.approx(200.0)
        assert lon == pytest.approx(100.0)


# ---------------------------------------------------------------------------
# Signal phase summarisation tests
# ---------------------------------------------------------------------------

class TestSignalPhaseSummarisation:
    """Test that SUMO multi-lane phase strings are summarised correctly."""

    def setup_method(self):
        self.normalizer = SumoNormalizer()
        self.now = datetime.now(timezone.utc)

    def _normalize_signal(self, phase_string: str) -> SignalPhase:
        raw = {"phase_string": phase_string, "phase_remaining_s": 10.0}
        state = self.normalizer.normalize_signal_state(
            tls_id="tls1",
            raw=raw,
            position=(12.9, 77.5),
            timestamp=self.now,
            scenario_run_id="run1",
            source="sumo_traci",
        )
        return state.signal_phase

    def test_all_green_is_green(self):
        assert self._normalize_signal("GGGG") == SignalPhase.green

    def test_all_red_is_red(self):
        assert self._normalize_signal("rrrr") == SignalPhase.red

    def test_all_yellow_is_yellow(self):
        assert self._normalize_signal("yyyy") == SignalPhase.yellow

    def test_mixed_green_and_red_is_green(self):
        """Green takes priority over red in a mixed phase string."""
        assert self._normalize_signal("GGrr") == SignalPhase.green

    def test_empty_phase_string_is_off(self):
        assert self._normalize_signal("") == SignalPhase.off

    def test_off_phase_character(self):
        assert self._normalize_signal("OOOO") == SignalPhase.off

    def test_yellow_beats_red(self):
        """Yellow takes priority over red."""
        assert self._normalize_signal("yrr") == SignalPhase.yellow


# ---------------------------------------------------------------------------
# DEFAULT_TYPE_MAP coverage test
# ---------------------------------------------------------------------------

class TestDefaultTypeMap:
    """Smoke-test that DEFAULT_TYPE_MAP covers all canonical VehicleType values."""

    def test_all_vehicle_types_covered(self):
        """Every canonical VehicleType must appear as a value in DEFAULT_TYPE_MAP."""
        mapped_types = set(DEFAULT_TYPE_MAP.values())
        for vt in VehicleType:
            assert vt in mapped_types, f"VehicleType.{vt.value} not in DEFAULT_TYPE_MAP"
