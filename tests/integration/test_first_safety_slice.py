"""First vertical-slice acceptance test.

This proves the deployable path uses ordinary adapter-shaped input rather than
a hard-coded demo: a SUMO-normalized vehicle event is bridged into the shared
canonical model, evaluated by Hrishi's emergency-braking policy, prioritized,
and served through the gateway's mounted safety API.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from services.gateway.app import app as gateway_app
from services.integration.canonical_bridge import world_state_from_adapter_events
from services.simulation_adapter.normalizer import SumoNormalizer


def _make_event(
    normalizer: SumoNormalizer,
    vehicle_id: str,
    acceleration: float,
    timestamp: datetime,
    speed: float = 18.0,
) -> dict:
    state = normalizer.normalize_vehicle_state(
        vehicle_id=vehicle_id,
        raw={
            "x": 25.0,
            "y": 10.0,
            "speed": speed,
            "angle": 0.0,
            "type_id": "car",
            "acceleration": acceleration,
        },
        timestamp=timestamp,
        scenario_run_id="any-valid-run",
        source="simulation-adapter",
    )
    return {
        "event_type": "actor.state.updated",
        "timestamp_utc": timestamp.isoformat(),
        "source": "simulation-adapter",
        "payload": state.model_dump(mode="json"),
    }


def test_adapter_to_safety_alert_path_uses_gateway_and_canonical_contracts() -> None:
    timestamp = datetime(2026, 9, 5, tzinfo=UTC)
    normalizer = SumoNormalizer(origin_lat=12.9716, origin_lon=77.5946)
    adapter_state = normalizer.normalize_vehicle_state(
        vehicle_id="vehicle-42",
        raw={
            "x": 25.0,
            "y": 10.0,
            "speed": 18.0,
            "angle": 0.0,
            "type_id": "car",
            "acceleration": -6.0,
        },
        timestamp=timestamp,
        scenario_run_id="any-valid-run",
        source="simulation-adapter",
    )
    adapter_event = {
        "event_type": "actor.state.updated",
        "timestamp_utc": timestamp,
        "source": "simulation-adapter",
        "payload": adapter_state.model_dump(mode="json"),
    }
    world_state = world_state_from_adapter_events([adapter_event])
    vehicle = world_state["vehicles"][0]

    assert vehicle.actor_id == "vehicle-42"
    assert vehicle.position.lat != 10.0  # The adapter's Cartesian value did not leak through.
    assert vehicle.source.value == "SIMULATION"

    with TestClient(gateway_app) as client:
        detector_response = client.get("/safety/v1/detectors")
        assert detector_response.status_code == 200
        assert len(detector_response.json()) == 10

        config = client.get("/safety/v1/config").json()
        config["emergency_braking"]["min_duration_s"] = 0.0
        assert client.put("/safety/v1/config", json=config).status_code == 200

        evaluation = client.post(
            "/safety/v1/evaluate/emergency_braking",
            json={"world_state": {"vehicles": [vehicle.model_dump(mode="json")]}},
        )
        assert evaluation.status_code == 200
        body = evaluation.json()
        assert body["errors"] == []
        assert len(body["risks"]) == 1
        risk = body["risks"][0]
        assert risk["type"] == "EMERGENCY_BRAKING"
        assert risk["evidence"][0]["acceleration_mps2"] == -6.0

        prioritized = client.post(
            "/safety/v1/alerts/prioritize",
            json={"risks": [risk], "active_alerts": [], "actor_states": {}},
        )
        assert prioritized.status_code == 200
        alert = prioritized.json()["alerts"][0]
        assert alert["risk_id"] == risk["risk_id"]
        assert alert["affected_actor_ids"] == ["vehicle-42"]
        assert alert["confidence"] > 0


def test_multi_actor_risk_scenario_and_world_state_websocket() -> None:
    """Two vehicles each emergency-braking → two risks → two alerts → WebSocket snapshot."""
    timestamp = datetime(2026, 9, 5, tzinfo=UTC)
    normalizer = SumoNormalizer(origin_lat=12.9716, origin_lon=77.5946)

    events = [
        _make_event(normalizer, "bus-north", acceleration=-9.0, timestamp=timestamp, speed=22.0),
        _make_event(normalizer, "auto-south", acceleration=-7.5, timestamp=timestamp, speed=15.0),
    ]

    world_state = world_state_from_adapter_events(events)
    assert len(world_state["vehicles"]) == 2
    actor_ids = {v.actor_id for v in world_state["vehicles"]}
    assert actor_ids == {"bus-north", "auto-south"}

    with TestClient(gateway_app) as client:
        # Lower threshold so both vehicles trigger.
        config = client.get("/safety/v1/config").json()
        config["emergency_braking"]["min_duration_s"] = 0.0
        client.put("/safety/v1/config", json=config)

        # Both vehicles hit the all-detector endpoint.
        evaluate_resp = client.post(
            "/safety/v1/evaluate",
            json={"world_state": {"vehicles": [v.model_dump(mode="json") for v in world_state["vehicles"]]}},
        )
        assert evaluate_resp.status_code == 200
        body = evaluate_resp.json()
        assert body["errors"] == []
        braking_risks = [r for r in body["risks"] if r["type"] == "EMERGENCY_BRAKING"]
        assert len(braking_risks) == 2, f"Expected 2 braking risks, got {len(braking_risks)}: {body['risks']}"

        # Both risks produce alerts.
        prioritize_resp = client.post(
            "/safety/v1/alerts/prioritize",
            json={"risks": braking_risks, "active_alerts": [], "actor_states": {}},
        )
        assert prioritize_resp.status_code == 200
        alerts = prioritize_resp.json()["alerts"]
        assert len(alerts) == 2
        alerted_ids = {a["affected_actor_ids"][0] for a in alerts}
        assert alerted_ids == {"bus-north", "auto-south"}

        # Ingest events into the world-state store.
        ingest_resp = client.post("/v1/world-state/ingest", json={"events": events})
        assert ingest_resp.status_code == 200
        ingest_body = ingest_resp.json()
        assert ingest_body["updated"] == 2
        assert ingest_body["errors"] == 0

        # REST snapshot reflects ingested actors.
        snap_resp = client.get("/v1/world-state/snapshot")
        assert snap_resp.status_code == 200
        snap = snap_resp.json()
        assert len(snap["actors"]) >= 2
        snap_ids = {a["actor_id"] for a in snap["actors"]}
        assert {"bus-north", "auto-south"}.issubset(snap_ids)

        # WebSocket delivers the current snapshot immediately on connect.
        with client.websocket_connect("/v1/world-state/stream") as ws:
            ws_snap = ws.receive_json()
            assert "actors" in ws_snap
            ws_ids = {a["actor_id"] for a in ws_snap["actors"]}
            assert {"bus-north", "auto-south"}.issubset(ws_ids)
