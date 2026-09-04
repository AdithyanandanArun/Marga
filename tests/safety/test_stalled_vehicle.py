"""Tests for StalledVehicleDetector.

Validates detection of stalled-in-lane vehicles, congestion suppression,
duration requirements, surrounding flow checks, and escalation behaviour.

The detector uses wall-clock time internally. Tests manipulate the
detector's internal ``_stopped_state`` to simulate time passage.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from packages.geo.helpers import point_along_bearing
from packages.safety_policies.config import PolicyConfig
from packages.schemas.canonical import Position, RiskType, VehicleState
from services.safety_detectors.detectors.stalled_vehicle import StalledVehicleDetector

from tests.safety.conftest import make_segment, make_vehicle_state
from tests.safety.fixtures.scenarios import create_stalled_scenario


def _uid() -> str:
    import uuid
    return str(uuid.uuid4())


class TestStalledVehicle:
    """Stalled vehicle detector acceptance tests."""

    def _make_ws(
        self,
        stalled_vs: VehicleState,
        flowing: list[VehicleState],
        seg_id: str,
    ) -> dict[str, Any]:
        return {
            "vehicles": [stalled_vs] + flowing,
            "road_network": {
                "segments": [make_segment(seg_id, 0.0)],
            },
        }

    def test_stalled_vehicle_detected(self, policy_config: PolicyConfig) -> None:
        """A stopped vehicle with flowing surroundings should be detected
        after the stopped-duration threshold."""
        detector = StalledVehicleDetector(policy_config)
        scenario = create_stalled_scenario(
            stopped_duration_s=60.0,
            surrounding_flow_mps=10.0,
        )
        # First call: seeds stopped_since
        detector.evaluate(scenario)
        # Backdate stopped_since to simulate 60s having passed
        for state in detector._stopped_state.values():
            state["stopped_since"] = datetime.now(timezone.utc) - timedelta(seconds=60.0)
        # Second call: should now detect
        risks = detector.evaluate(scenario)
        assert len(risks) > 0
        assert all(r.type == RiskType.STALLED_VEHICLE for r in risks)

    def test_congestion_not_stalled(self, policy_config: PolicyConfig) -> None:
        """If surrounding traffic is also stopped (congestion), no stall alert."""
        detector = StalledVehicleDetector(policy_config)
        scenario = create_stalled_scenario(
            surrounding_flow_mps=0.0,
        )
        # First call: seeds stopped_since
        detector.evaluate(scenario)
        # Backdate
        for state in detector._stopped_state.values():
            state["stopped_since"] = datetime.now(timezone.utc) - timedelta(seconds=60.0)
        # Second call
        risks = detector.evaluate(scenario)
        assert len(risks) == 0

    def test_duration_requirement(self, policy_config: PolicyConfig) -> None:
        """Stopped less than min_stopped_duration_s should not trigger."""
        detector = StalledVehicleDetector(policy_config)
        scenario = create_stalled_scenario(surrounding_flow_mps=10.0)
        # Only one evaluate - stopped_since = now, duration ~ 0
        risks = detector.evaluate(scenario)
        assert len(risks) == 0

    def test_surrounding_flow_check(self, policy_config: PolicyConfig) -> None:
        """Vehicle stopped with flowing neighbours vs. all-stopped neighbours."""
        seg_id = _uid()
        base_lat, base_lon = 12.9716, 77.5946
        stalled = make_vehicle_state(
            actor_id="stalled-1",
            lat=base_lat,
            lon=base_lon,
            speed_mps=0.0,
            road_segment_id=seg_id,
        )
        flowing = []
        for i in range(3):
            lat, lon = point_along_bearing(base_lat, base_lon, 0.0, 30.0 * (i + 1))
            flowing.append(
                make_vehicle_state(
                    lat=lat, lon=lon, speed_mps=10.0, road_segment_id=seg_id,
                )
            )

        detector = StalledVehicleDetector(policy_config)
        ws = self._make_ws(stalled, flowing, seg_id)

        # First call: seed stopped state
        detector.evaluate(ws)
        # Backdate
        for state in detector._stopped_state.values():
            state["stopped_since"] = datetime.now(timezone.utc) - timedelta(seconds=60.0)
        # Should detect because flowing traffic speed > threshold
        risks = detector.evaluate(ws)
        assert len(risks) > 0

        # Now test with all stopped neighbours (congestion)
        detector2 = StalledVehicleDetector(policy_config)
        slow_neighbours = []
        for i in range(3):
            lat, lon = point_along_bearing(base_lat, base_lon, 0.0, 30.0 * (i + 1))
            slow_neighbours.append(
                make_vehicle_state(
                    lat=lat, lon=lon, speed_mps=0.0, road_segment_id=seg_id,
                )
            )
        ws2 = self._make_ws(stalled, slow_neighbours, seg_id)
        detector2.evaluate(ws2)
        for state in detector2._stopped_state.values():
            state["stopped_since"] = datetime.now(timezone.utc) - timedelta(seconds=60.0)
        risks2 = detector2.evaluate(ws2)
        assert len(risks2) == 0

    def test_escalation(self, policy_config: PolicyConfig) -> None:
        """Repeated stall detections should escalate severity."""
        detector = StalledVehicleDetector(policy_config)
        scenario = create_stalled_scenario(surrounding_flow_mps=10.0)

        # First detection
        detector.evaluate(scenario)
        for state in detector._stopped_state.values():
            state["stopped_since"] = datetime.now(timezone.utc) - timedelta(seconds=60.0)
        risks1 = detector.evaluate(scenario)
        assert len(risks1) > 0
        sev1 = risks1[0].severity

        # Second detection (escalated)
        risks2 = detector.evaluate(scenario)
        assert len(risks2) > 0
        sev2 = risks2[0].severity
        assert sev2 >= sev1
