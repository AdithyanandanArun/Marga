"""Contract coverage for the canonical live mobility graph."""

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from packages.schemas.canonical import ActorType, Position, SourceType, VehicleState
from packages.schemas.mobility_graph import GraphEdgeDefinition
from services.gateway.app import app
from services.mobility_graph.service import MobilityGraphService


def vehicle(actor_id: str, edge_id: str, *, actor_type: ActorType = ActorType.CAR, speed: float = 5.0) -> VehicleState:
    return VehicleState(
        actor_id=actor_id,
        actor_type=actor_type,
        ts=datetime(2026, 9, 5, 12, 0, tzinfo=UTC),
        position=Position(lat=12.9716, lon=77.5946),
        position_uncertainty_m=2.0,
        speed_mps=speed,
        heading_deg=90.0,
        road_segment_id=edge_id,
        lane_id=f"{edge_id}_0",
        source=SourceType.SIMULATION,
    )


def test_edge_metrics_are_canonical_confidence_aware_and_rolling() -> None:
    graph = MobilityGraphService()
    graph.register_edge(
        GraphEdgeDefinition(
            edge_id="north-in", intersection_id="j1", lane_count=2, capacity_vehicles=4, source="sumo"
        )
    )
    graph.register_edge(
        GraphEdgeDefinition(edge_id="south-out", intersection_id="j1", capacity_vehicles=4, source="sumo")
    )
    graph.observe_vehicle(vehicle("car-1", "north-in", speed=1.0))
    graph.observe_vehicle(vehicle("bike-1", "north-in", actor_type=ActorType.BIKE, speed=3.0))
    state = graph.get_edge("north-in")

    assert state is not None
    assert state.vehicle_count == 2
    assert state.queue_length == 1
    assert state.two_wheeler_ratio == 0.5
    assert state.capacity_ratio == 0.5
    assert 0 < state.gps_confidence < 1
    assert set(state.rolling_windows) == {"5", "15", "30", "60"}
    degraded = graph.update_position_quality("car-1", 25.0, datetime.now(UTC))
    assert degraded is not None
    assert degraded.gps_confidence < state.gps_confidence
    intersection = graph.get_intersection("j1")
    assert intersection is not None
    assert intersection.vehicle_count == 2


def test_edge_flow_expires_outside_rolling_window() -> None:
    graph = MobilityGraphService()
    graph.register_edge(GraphEdgeDefinition(edge_id="e1", source="sumo"))
    now = datetime.now(UTC)
    graph.observe_vehicle(vehicle("car-1", "e1").model_copy(update={"ts": now}))
    graph._entry_times["e1"].append((datetime.now(UTC) - timedelta(seconds=61), "old-car"))  # noqa: SLF001
    state = graph.get_edge("e1")
    assert state is not None
    assert state.rolling_windows["60"].flow_rate_vph == 60.0


def test_gateway_exposes_graph_edge_and_stream_contract() -> None:
    with TestClient(app) as client:
        registered = client.post(
            "/graph/edges",
            json={
                "edge_id": "api-edge",
                "intersection_id": "api-junction",
                "capacity_vehicles": 10,
                "source": "test",
            },
        )
        assert registered.status_code == 201
        with client.websocket_connect("/graph/stream") as ws:
            response = client.post(
                "/v1/ingest/vehicle-state",
                json=vehicle("api-car", "api-edge").model_dump(mode="json"),
            )
            assert response.status_code == 202
            event = ws.receive_json()
            assert event["event_type"] == "graph.edge.updated"
            assert event["data"]["edge_id"] == "api-edge"
        edge = client.get("/graph/edges/api-edge")
        intersection = client.get("/graph/intersections/api-junction")
        assert edge.status_code == 200
        assert edge.json()["vehicle_count"] >= 1
        assert intersection.status_code == 200


def test_gateway_maps_adapter_pedestrian_to_its_graph_edge() -> None:
    with TestClient(app) as client:
        assert client.post("/graph/edges", json={"edge_id": "ped-edge", "source": "test"}).status_code == 201
        response = client.post(
            "/v1/world-state/ingest",
            json={
                "events": [
                    {
                        "event_type": "actor.state.updated",
                        "timestamp_utc": "2026-09-05T12:00:00Z",
                        "payload": {
                            "pedestrian_id": "ped-graph",
                            "position": {"lat": 12.97, "lon": 77.59, "uncertainty_m": 1.0},
                            "speed_mps": 1.2,
                            "heading_deg": 45,
                            "road_segment_id": "ped-edge",
                        },
                    }
                ]
            },
        )
        assert response.status_code == 200
        assert client.get("/graph/edges/ped-edge").json()["pedestrian_count"] == 1
