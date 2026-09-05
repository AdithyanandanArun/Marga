"""Contract tests for graph -> RL -> safety -> adapter signal control."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient
from marga_signal_rl.actions import SignalAction

from packages.schemas.canonical import ActorType, Position, SourceType, TrafficSignalState, VehicleState
from packages.schemas.mobility_graph import GraphEdgeDefinition
from packages.schemas.signal_control import SignalApproachTopology, SignalJunctionTopology
from services.gateway.app import app
from services.gateway.signal_control import (
    _queue_command,
    register_signal_executor,
    reset_pending_commands,
    signal_controller,
)
from services.mobility_graph import mobility_graph


def _vehicle(actor_id: str, edge_id: str, speed_mps: float) -> VehicleState:
    return VehicleState(
        actor_id=actor_id,
        actor_type=ActorType.BIKE,
        ts=datetime.now(UTC),
        position=Position(lat=12.9716, lon=77.5946),
        position_uncertainty_m=2.0,
        speed_mps=speed_mps,
        heading_deg=0.0,
        road_segment_id=edge_id,
        lane_id=f"{edge_id}_0",
        source=SourceType.SIMULATION,
    )


def _reset() -> None:
    mobility_graph.__init__()
    signal_controller.clear()
    reset_pending_commands()
    # Other tests swap the executor; restore the shipped one so the queue that
    # delivers applied actions to the simulator is what is under test.
    register_signal_executor(_queue_command)


def test_live_signal_decision_uses_graph_evidence_and_adapter_executor() -> None:
    _reset()
    mobility_graph.register_edge(GraphEdgeDefinition(edge_id="north-in", intersection_id="junction-1", source="sumo"))
    mobility_graph.register_edge(GraphEdgeDefinition(edge_id="south-in", intersection_id="junction-1", source="sumo"))
    mobility_graph.observe_vehicle(_vehicle("bike-n", "north-in", speed_mps=0.5))
    mobility_graph.observe_vehicle(_vehicle("bike-s", "south-in", speed_mps=5.0))
    topology = SignalJunctionTopology(
        junction_id="junction-1",
        signal_id="tls-1",
        approaches=[
            SignalApproachTopology(movement_id="N", incoming_edge_ids=["north-in"]),
            SignalApproachTopology(movement_id="S", incoming_edge_ids=["south-in"]),
        ],
        phase_index_by_name={"NS_GREEN": 0},
        source="sumo",
    )
    signal_controller.register_topology(topology)
    signal_controller.observe_signal(
        TrafficSignalState(
            signal_id="tls-1",
            intersection_id="junction-1",
            ts=datetime.now(UTC),
            position=Position(lat=12.9716, lon=77.5946),
            current_phase="NS_GREEN",
            phase_remaining_s=20.0,
            source=SourceType.SIMULATION,
        )
    )

    commands: list[dict[str, object]] = []
    register_signal_executor(commands.append)
    decision = signal_controller.recommend("junction-1", SignalAction.EXTEND_GREEN_5)
    decision = signal_controller.apply(decision.decision_id)

    assert decision.applied
    assert decision.application_error is None
    assert decision.confidence > 0
    assert "live-mobility-graph" in decision.provenance
    assert any(item["type"] == "graph_edge_ids" for item in decision.evidence)
    assert commands and commands[0]["signal_id"] == "tls-1"


def test_gateway_exposes_graph_driven_signal_endpoints() -> None:
    _reset()
    mobility_graph.register_edge(GraphEdgeDefinition(edge_id="east-in", intersection_id="junction-2", source="sumo"))
    mobility_graph.observe_vehicle(_vehicle("bike-e", "east-in", speed_mps=0.4))
    with TestClient(app) as client:
        topology_response = client.post(
            "/v1/signals/topologies",
            json={
                "junction_id": "junction-2",
                "signal_id": "tls-2",
                "approaches": [{"movement_id": "E", "incoming_edge_ids": ["east-in"]}],
                "source": "sumo",
            },
        )
        assert topology_response.status_code == 201
        signal_response = client.post(
            "/v1/ingest/signal-state",
            json={
                "signal_id": "tls-2",
                "intersection_id": "junction-2",
                "ts": datetime.now(UTC).isoformat(),
                "position": {"lat": 12.9716, "lon": 77.5946},
                "current_phase": "EW_GREEN",
                "phase_remaining_s": 20.0,
                "source": "SIMULATION",
            },
        )
        assert signal_response.status_code == 202
        state_response = client.get("/v1/signals/junction-2/state")
        assert state_response.status_code == 200
        assert state_response.json()["total_queue"] == 1
        recommendation = client.post("/v1/signals/junction-2/recommend")
        assert recommendation.status_code == 200
        body = recommendation.json()
        assert body["decision"]["policy_version"] == "tabular-q-learning-v1"
        assert body["decision"]["evidence"]


def test_applied_action_is_queued_for_the_simulator_to_drain() -> None:
    """An applied RL action must reach a simulator that cannot be called directly."""
    _reset()
    mobility_graph.register_edge(GraphEdgeDefinition(edge_id="west-in", intersection_id="junction-3", source="sumo"))
    mobility_graph.observe_vehicle(_vehicle("bike-w", "west-in", speed_mps=0.4))
    with TestClient(app) as client:
        assert client.post(
            "/v1/signals/topologies",
            json={
                "junction_id": "junction-3",
                "signal_id": "tls-3",
                "approaches": [{"movement_id": "W", "incoming_edge_ids": ["west-in"]}],
                "source": "junction-network-simulator",
            },
        ).status_code == 201
        assert "junction-3" in client.get("/v1/signals/topologies").json()["junction_ids"]
        assert client.post(
            "/v1/ingest/signal-state",
            json={
                "signal_id": "tls-3",
                "intersection_id": "junction-3",
                "ts": datetime.now(UTC).isoformat(),
                "position": {"lat": 12.9716, "lon": 77.5946},
                "current_phase": "EW_GREEN",
                "phase_remaining_s": 20.0,
                "source": "SIMULATION",
            },
        ).status_code == 202

        applied = client.post("/v1/signals/junction-3/apply", json={"action": "EXTEND_GREEN_5"})
        assert applied.status_code == 200
        assert applied.json()["decision"]["applied"] is True
        assert applied.json()["decision"]["application_error"] is None

        drained = client.get("/v1/signals/commands/pending").json()
        assert drained["count"] == 1
        command = drained["commands"][0]
        assert command["signal_id"] == "tls-3"
        assert command["action"] == "EXTEND_GREEN_5"
        assert command["duration_s"] > 20.0

        # Draining is destructive so the simulator never replays an action.
        assert client.get("/v1/signals/commands/pending").json()["count"] == 0
