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
