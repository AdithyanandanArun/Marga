"""Edge cost function and reroute trigger tests."""

import pytest

from marga_routing.cost import (
    CLOSURE_COST,
    CONGESTION_THRESHOLD,
    CRITICAL_HAZARD_THRESHOLD,
    MATERIAL_IMPROVEMENT_RATIO,
    edge_cost,
    should_reroute,
)
from marga_routing.graph import Edge, EdgeMetrics


def _edge(
    length_m: float = 200.0,
    speed: float = 8.0,
    capacity_ratio: float = 0.0,
    hazard: float = 0.0,
    confidence: float = 1.0,
    closure: bool = False,
    downstream: float = 0.0,
) -> Edge:
    return Edge(
        edge_id="test",
        src="A",
        dst="B",
        length_m=length_m,
        metrics=EdgeMetrics(
            avg_speed_mps=speed,
            capacity_ratio=capacity_ratio,
            hazard_penalty=hazard,
            gps_confidence=confidence,
            closure=closure,
            downstream_congestion=downstream,
        ),
    )


class TestEdgeCost:
    def test_free_flow_is_travel_time(self):
        e = _edge(length_m=100.0, speed=10.0)
        cost = edge_cost(e)
        assert cost == pytest.approx(10.0, rel=0.01)

    def test_closure_returns_high_cost(self):
        e = _edge(closure=True)
        assert edge_cost(e) >= CLOSURE_COST

    def test_full_saturation_closure(self):
        e = _edge(speed=0.1, capacity_ratio=1.0)
        assert edge_cost(e) >= CLOSURE_COST

    def test_congestion_penalty_applied_above_threshold(self):
        free = edge_cost(_edge(capacity_ratio=CONGESTION_THRESHOLD - 0.1))
        busy = edge_cost(_edge(capacity_ratio=0.95))
        assert busy > free

    def test_no_congestion_below_threshold(self):
        e = _edge(capacity_ratio=CONGESTION_THRESHOLD - 0.05)
        free_flow = e.length_m / e.metrics.avg_speed_mps
        cost = edge_cost(e)
        assert cost == pytest.approx(free_flow, rel=0.01)

    def test_hazard_penalty_adds_cost(self):
        no_hazard = edge_cost(_edge(hazard=0.0))
        high_hazard = edge_cost(_edge(hazard=1.0))
        assert high_hazard > no_hazard

    def test_gps_uncertainty_adds_cost(self):
        good_gps = edge_cost(_edge(confidence=1.0))
        bad_gps = edge_cost(_edge(confidence=0.0))
        assert bad_gps > good_gps

    def test_load_offset_increases_cost(self):
        e = _edge(capacity_ratio=0.5)
        e.capacity_vehicles = 10
        base = edge_cost(e, load_offset=0)
        loaded = edge_cost(e, load_offset=10)
        assert loaded > base


class TestShouldReroute:
    def test_closure_always_triggers(self):
        triggered, reason = should_reroute(100, 50, 0.0, edge_closed=True)
        assert triggered
        assert reason == "closure"

    def test_critical_hazard_triggers(self):
        triggered, reason = should_reroute(100, 50, CRITICAL_HAZARD_THRESHOLD + 0.1, False)
        assert triggered
        assert reason == "critical_hazard"

    def test_material_improvement_triggers(self):
        old = 100.0
        new = old * (1 - MATERIAL_IMPROVEMENT_RATIO - 0.01)
        triggered, reason = should_reroute(old, new, 0.0, False)
        assert triggered
        assert reason == "material_eta_improvement"

    def test_small_improvement_does_not_trigger(self):
        old = 100.0
        new = old * (1 - MATERIAL_IMPROVEMENT_RATIO + 0.05)  # improvement < threshold
        triggered, _ = should_reroute(old, new, 0.0, False)
        assert not triggered

    def test_no_trigger_when_worse(self):
        triggered, _ = should_reroute(50.0, 80.0, 0.0, False)
        assert not triggered
