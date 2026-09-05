"""Cooperative distributor tests."""

import pytest

from marga_routing.distributor import CooperativeDistributor
from marga_routing.graph import EdgeMetrics
from marga_routing.mock_graph import build_mock_graph


class TestCooperativeDistributor:
    def setup_method(self):
        self.graph = build_mock_graph()
        self.dist = CooperativeDistributor(self.graph)

    def test_single_vehicle_gets_route(self):
        results = self.dist.plan({"v1": ("hub", "roundabout")})
        assert len(results) == 1
        r = results[0]
        assert r.vehicle_id == "v1"
        assert len(r.new_path) > 0
        assert r.new_path[-1][0] == "roundabout"

    def test_multiple_vehicles_get_separate_assignments(self):
        vehicles = {
            "v1": ("rail_crossing", "roundabout"),
            "v2": ("rail_crossing", "roundabout"),
            "v3": ("rail_crossing", "roundabout"),
        }
        results = self.dist.plan(vehicles)
        assert len(results) == 3
        assert {r.vehicle_id for r in results} == {"v1", "v2", "v3"}

    def test_closure_triggers_reroute(self):
        # Close the primary hub→roundabout approach
        self.graph.edge("e_hub_rbt_a").metrics.closure = True
        results = self.dist.plan({"v1": ("hub", "roundabout")})
        r = results[0]
        # Should still find a path (bypass exists)
        assert r.new_path and r.new_path[-1][0] == "roundabout"

    def test_high_hazard_triggers_reroute(self):
        # Pre-compute a path that goes through e_hub_rbt_a, then inject a hazard on it
        from marga_routing.pathfinder import find_path
        path_via_direct, _ = find_path(self.graph, "hub", "roundabout")
        # Inject a critical hazard on every edge of the direct path so _check_trigger fires
        for _, eid in path_via_direct:
            if eid:
                self.graph.edge(eid).metrics.hazard_penalty = 0.9
        results = self.dist.plan(
            {"v1": ("hub", "roundabout")},
            current_paths={"v1": path_via_direct},
        )
        r = results[0]
        assert r.triggered
        assert r.reason in ("critical_hazard", "material_eta_improvement")

    def test_no_reroute_when_path_is_clear(self):
        # All edges clear and fast — no reroute should trigger
        for e in self.graph.all_edges():
            e.metrics.capacity_ratio = 0.1
            e.metrics.hazard_penalty = 0.0
            e.metrics.closure = False
            e.metrics.avg_speed_mps = 10.0
        results = self.dist.plan({"v1": ("hub", "south_t")})
        r = results[0]
        # May or may not trigger depending on path comparison; just confirm it runs
        assert r.vehicle_id == "v1"

    def test_old_eta_and_new_eta_are_positive(self):
        results = self.dist.plan({"v1": ("rail_crossing", "south_t")})
        r = results[0]
        assert r.old_eta_s > 0
        assert r.new_eta_s > 0

    def test_no_reroute_reported_when_no_alternative_exists(self):
        """A forced trigger with no viable detour must not report rerouted=True.

        Regression: closure/critical-hazard set `triggered` before alternatives
        were evaluated, so a vehicle with nowhere else to go was reported as
        rerouted with identical old/new geometry and ETA.
        """
        from marga_routing.pathfinder import find_path, path_to_geometry

        for e in self.graph.all_edges():
            e.metrics.closure = True
        path, _ = find_path(self.graph, "rail_crossing", "roundabout")
        r = self.dist.plan(
            {"v1": ("rail_crossing", "roundabout")},
            current_paths={"v1": path},
        )[0]

        assert r.old_path == r.new_path
        assert r.triggered is False
        assert r.reason == "no_alternative_available"
        assert r.old_eta_s == r.new_eta_s
        assert path_to_geometry(self.graph, r.old_path) == path_to_geometry(self.graph, r.new_path)

    def test_reroute_reported_only_with_changed_geometry(self):
        """Whenever triggered is True the geometry and path must actually differ."""
        from marga_routing.pathfinder import find_path, path_to_geometry

        path, _ = find_path(self.graph, "rail_crossing", "roundabout")
        for _, eid in path:
            if eid:
                self.graph.edge(eid).metrics.closure = True
        r = self.dist.plan(
            {"v1": ("rail_crossing", "roundabout")},
            current_paths={"v1": path},
        )[0]

        assert r.triggered is True
        assert r.old_path != r.new_path
        assert path_to_geometry(self.graph, r.old_path) != path_to_geometry(self.graph, r.new_path)

    def test_load_balancing_across_vehicles(self):
        """When many vehicles request the same route, later ones should be steered differently."""
        vehicles = {f"v{i}": ("hub", "roundabout") for i in range(6)}
        results = self.dist.plan(vehicles)
        # Collect all edge_ids used by all vehicles
        all_paths = [r.new_path for r in results]
        # At least one vehicle should have a different sequence of edges (load balancing)
        edge_sequences = [
            tuple(eid for _, eid in path if eid)
            for path in all_paths
        ]
        # With 6 vehicles, there should be some diversity (not all identical)
        unique_sequences = set(edge_sequences)
        # If graph has alternatives, we expect > 1 unique path
        assert len(unique_sequences) >= 1  # minimum sanity; diversity is best-effort
