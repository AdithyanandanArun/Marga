"""A* pathfinder tests."""

import pytest

from marga_routing.graph import Edge, EdgeMetrics, MobilityGraph, Node
from marga_routing.mock_graph import build_mock_graph
from marga_routing.pathfinder import find_path, path_eta_s, path_to_geometry


def _simple_graph() -> MobilityGraph:
    """A → B → C triangle with one shortcut A → C."""
    g = MobilityGraph()
    for nid, lat, lon in [("A", 12.0, 77.0), ("B", 12.001, 77.001), ("C", 12.0, 77.002)]:
        g.add_node(Node(nid, lat, lon))
    g.add_edge(Edge("AB", "A", "B", 150, metrics=EdgeMetrics(avg_speed_mps=10.0)))
    g.add_edge(Edge("BC", "B", "C", 150, metrics=EdgeMetrics(avg_speed_mps=10.0)))
    g.add_edge(Edge("AC", "A", "C", 310, metrics=EdgeMetrics(avg_speed_mps=10.0)))  # longer but direct
    return g


class TestFindPath:
    def test_finds_shortest_path(self):
        g = _simple_graph()
        path, cost = find_path(g, "A", "C")
        node_ids = [n for n, _ in path]
        assert "C" in node_ids
        assert cost < float("inf")

    def test_no_path_returns_empty(self):
        g = MobilityGraph()
        g.add_node(Node("X", 12.0, 77.0))
        g.add_node(Node("Y", 12.1, 77.1))
        path, cost = find_path(g, "X", "Y")
        assert path == []
        assert cost == float("inf")

    def test_same_origin_destination(self):
        g = _simple_graph()
        path, cost = find_path(g, "A", "A")
        assert path == [("A", None)]
        assert cost == 0.0

    def test_prefers_faster_edge(self):
        g = MobilityGraph()
        g.add_node(Node("A", 12.0, 77.0))
        g.add_node(Node("B", 12.0, 77.001))
        g.add_node(Node("C", 12.0, 77.002))
        g.add_node(Node("D", 12.0, 77.003))
        # Route via B: 300m at 2 m/s = 150s total
        g.add_edge(Edge("AB", "A", "B", 150, metrics=EdgeMetrics(avg_speed_mps=2.0)))
        g.add_edge(Edge("BD", "B", "D", 150, metrics=EdgeMetrics(avg_speed_mps=2.0)))
        # Route via C: 300m at 15 m/s = 20s total  ← should win
        g.add_edge(Edge("AC", "A", "C", 150, metrics=EdgeMetrics(avg_speed_mps=15.0)))
        g.add_edge(Edge("CD", "C", "D", 150, metrics=EdgeMetrics(avg_speed_mps=15.0)))
        path, cost = find_path(g, "A", "D")
        edge_ids = [eid for _, eid in path if eid]
        assert "AC" in edge_ids or "CD" in edge_ids

    def test_avoids_closure(self):
        g = _simple_graph()
        # Close the direct A→C edge
        g.edge("AC").metrics.closure = True
        path, cost = find_path(g, "A", "C")
        node_ids = [n for n, _ in path]
        assert "B" in node_ids   # forced to go via B

    def test_load_offset_raises_cost(self):
        # Build a graph where AC is the unique direct edge so load_offset on it is felt
        g = MobilityGraph()
        g.add_node(Node("A", 12.0, 77.0))
        g.add_node(Node("C", 12.0, 77.002))
        e = Edge("AC", "A", "C", 200, capacity_vehicles=10, metrics=EdgeMetrics(avg_speed_mps=10.0))
        g.add_edge(e)
        _, cost_no_load = find_path(g, "A", "C", load_offsets={})
        _, cost_with_load = find_path(g, "A", "C", load_offsets={"AC": 20})  # saturate
        assert cost_with_load > cost_no_load

    def test_mock_graph_hub_to_roundabout(self):
        g = build_mock_graph()
        path, cost = find_path(g, "hub", "roundabout")
        assert path and cost < float("inf")
        assert path[0][0] == "hub"
        assert path[-1][0] == "roundabout"

    def test_mock_graph_rail_to_south(self):
        g = build_mock_graph()
        path, cost = find_path(g, "rail_crossing", "south_t")
        assert path and cost < float("inf")

    def test_removed_cut_is_never_used_when_hub_corridor_is_congested(self):
        """Congestion cannot justify routing onto a road that was removed."""
        g = build_mock_graph()

        normal_path, _ = find_path(g, "rail_crossing", "roundabout")
        assert "hub" in [node_id for node_id, _ in normal_path]

        for edge_id in ("e_hub_rail_b", "e_hub_rbt_a"):
            g.edge(edge_id).metrics = EdgeMetrics(
                avg_speed_mps=1.0,
                vehicle_count=38,
                capacity_ratio=0.95,
                gps_confidence=0.92,
            )

        rerouted_path, _ = find_path(g, "rail_crossing", "roundabout")
        rerouted_edges = {edge_id for _, edge_id in rerouted_path if edge_id}
        assert "hub" in [node_id for node_id, _ in rerouted_path]
        assert not any(edge_id.startswith("e_cut_") for edge_id in rerouted_edges)


class TestPathHelpers:
    def test_path_to_geometry(self):
        g = _simple_graph()
        path, _ = find_path(g, "A", "C")
        geo = path_to_geometry(g, path)
        assert len(geo) >= 2
        assert all("lat" in pt and "lon" in pt for pt in geo)

    def test_path_eta_s(self):
        g = _simple_graph()
        path, _ = find_path(g, "A", "B")
        eta = path_eta_s(g, path)
        assert eta > 0

    def test_eta_zero_for_trivial_path(self):
        g = _simple_graph()
        path = [("A", None)]
        assert path_eta_s(g, path) == 0.0
