"""Acceptance coverage for Agent 1's canonical world-state safety slice."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from packages.schemas.canonical import Position, SourceType, VehicleState
from services.gateway.app import app
from services.position import PositionFusionService, predict_trajectory
from services.risk import RiskEngine


def _vehicle(actor_id: str, *, lat: float, lon: float, heading_deg: float) -> VehicleState:
    return VehicleState(
        actor_id=actor_id,
        ts=datetime(2026, 9, 5, 12, 0, tzinfo=UTC),
        position=Position(lat=lat, lon=lon),
        position_uncertainty_m=1.0,
        speed_mps=12.0,
        heading_deg=heading_deg,
        source=SourceType.SIMULATION,
    )


def test_fusion_and_trajectory_expand_uncertainty() -> None:
    first = _vehicle("fusion-actor", lat=12.9716, lon=77.5946, heading_deg=0)
    second = first.model_copy(
        update={
            "ts": datetime(2026, 9, 5, 12, 0, 2, tzinfo=UTC),
            "position": Position(lat=12.97161, lon=77.59461),
            "position_uncertainty_m": 4.0,
            "source": SourceType.RSU,
        }
    )
    fused = PositionFusionService().fuse([first, second])
    trajectory = predict_trajectory(fused, horizon_s=3.0)

    assert fused.position_uncertainty_m < first.position_uncertainty_m
    assert "position-fused" in fused.capabilities
    assert trajectory.points[-1].uncertainty_m > trajectory.points[0].uncertainty_m
    assert trajectory.points[-1].position.lat > trajectory.points[0].position.lat


def test_ttc_risk_is_explainable_and_canonical_ingest_streams_delta() -> None:
    first = _vehicle("ttc-north", lat=12.9716, lon=77.5946, heading_deg=90)
    second = _vehicle("ttc-south", lat=12.9716, lon=77.59468, heading_deg=270)
    direct_risk = RiskEngine().evaluate_pair(first, second)
    assert direct_risk is not None
    assert direct_risk.type.value == "HEAD_ON"
    assert direct_risk.time_to_conflict_s is not None
    assert direct_risk.evidence[0]["model"] == "constant_velocity_local_tangent_plane"

    with TestClient(app) as client:
        with client.websocket_connect("/v1/world-state/stream") as ws:
            initial = ws.receive_json()
            assert initial["kind"] == "snapshot"
            assert {"server_time", "upserts", "deletes"}.issubset(initial)

            assert client.post("/v1/ingest/vehicle-state", json=first.model_dump(mode="json")).status_code == 202
            assert client.post("/v1/ingest/vehicle-state", json=second.model_dump(mode="json")).status_code == 202
            delta = ws.receive_json()
            while not any(item["entity_type"] == "risk" for item in delta["upserts"]):
                delta = ws.receive_json()

        risk = next(item["data"] for item in delta["upserts"] if item["entity_type"] == "risk")
        trace = client.get(f"/v1/incidents/{risk['risk_id']}/trace")
        assert trace.status_code == 200
        assert trace.json()["risk"]["risk_id"] == risk["risk_id"]
        assert trace.json()["derived_metrics"]["time_to_conflict_s"] is not None


def test_pedestrian_and_hazard_canonical_endpoints() -> None:
    with TestClient(app) as client:
        pedestrian = {
            "actor_id": "ped-agent1",
            "ts": "2026-09-05T12:00:00Z",
            "position": {"lat": 12.9716, "lon": 77.5946},
            "position_uncertainty_m": 2.0,
            "speed_mps": 1.2,
            "heading_deg": 30.0,
        }
        hazard = {
            "hazard_id": "hazard-agent1",
            "timestamp_utc": "2026-09-05T12:00:00Z",
            "hazard_type": "pothole",
            "position": {"lat": 12.9716, "lon": 77.5946, "uncertainty_m": 2.0, "confidence": 0.8, "source": "rsu"},
            "confidence": 0.8,
            "reporting_source": "rsu-17",
        }
        assert client.post("/v1/ingest/pedestrian-state", json=pedestrian).status_code == 202
        response = client.post("/v1/ingest/hazard-observation", json=hazard)
        assert response.status_code == 202
        assert response.json()["entity"]["data"]["type"] == "POTHOLE"
