"""Unit tests for the Marga Alert Platform service."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from marga_schemas.alert import Alert, AlertPriority, AlertState
from marga_schemas.common import GeoPoint

from services.alerts.marga_alerts.audience import AudienceResolver
from services.alerts.marga_alerts.lifecycle import AlertLifecycleManager
from services.alerts.marga_alerts.prioritizer import AlertPrioritizer
from services.alerts.marga_alerts.store import AlertStore

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_alert(
    *,
    alert_type: str = "collision_warning",
    priority: AlertPriority = AlertPriority.HIGH,
    confidence: float = 0.9,
    actor_ids: list[str] | None = None,
    position: GeoPoint | None = None,
    ttl_s: int | None = None,
    expires_at: datetime | None = None,
    machine_reasoning: dict | None = None,
) -> Alert:
    return Alert(
        alert_id=uuid4(),
        alert_type=alert_type,
        priority=priority,
        title="Test alert",
        description="Test description",
        confidence=confidence,
        affected_actor_ids=actor_ids or [],
        position=position or GeoPoint(lat=12.97, lon=77.59),
        ttl_s=ttl_s,
        expires_at=expires_at,
        machine_reasoning=machine_reasoning or {},
    )


# ===================================================================
# Prioritizer tests
# ===================================================================


class TestAlertPrioritizer:
    def test_critical_collision_ranks_above_informational(self):
        """A CRITICAL collision alert must outrank an INFO road hazard."""
        p = AlertPrioritizer()

        critical = _make_alert(
            alert_type="collision_warning",
            priority=AlertPriority.CRITICAL,
            confidence=0.95,
            machine_reasoning={"time_to_conflict_s": 2.0, "collision_severity": 0.9},
        )
        info = _make_alert(
            alert_type="pothole",
            priority=AlertPriority.INFO,
            confidence=0.6,
            machine_reasoning={"collision_severity": 0.1},
        )

        result = p.prioritize([info, critical])
        assert result[0].alert_id == critical.alert_id
        assert result[1].alert_id == info.alert_id

    def test_compute_score_returns_float(self):
        p = AlertPrioritizer()
        alert = _make_alert()
        score = p.compute_score(alert)
        assert isinstance(score, float)
        assert score > 0

    def test_priority_ordering_is_consistent(self):
        """Higher priority enum always yields higher base score."""
        p = AlertPrioritizer()
        alerts = [
            _make_alert(priority=AlertPriority.INFO),
            _make_alert(priority=AlertPriority.LOW),
            _make_alert(priority=AlertPriority.MEDIUM),
            _make_alert(priority=AlertPriority.HIGH),
            _make_alert(priority=AlertPriority.CRITICAL),
        ]
        scores = [p.compute_score(a) for a in alerts]
        # Each score should be strictly increasing.
        for i in range(1, len(scores)):
            assert scores[i] > scores[i - 1], f"Score at index {i} not greater than {i - 1}"

    def test_custom_weights(self):
        """Weights can be overridden at construction time."""
        weights = {
            "time_to_conflict": 0.0,
            "collision_severity": 0.0,
            "confidence": 1.0,
            "braking_feasibility": 0.0,
            "actor_vulnerability": 0.0,
            "message_age": 0.0,
            "duplication_state": 0.0,
        }
        p = AlertPrioritizer(weights=weights)
        high_conf = _make_alert(priority=AlertPriority.MEDIUM, confidence=0.99)
        low_conf = _make_alert(priority=AlertPriority.MEDIUM, confidence=0.1)
        result = p.prioritize([low_conf, high_conf])
        assert result[0].alert_id == high_conf.alert_id

    def test_duplicate_flag_lowers_score(self):
        p = AlertPrioritizer()
        normal = _make_alert(machine_reasoning={"is_duplicate": False})
        dup = _make_alert(machine_reasoning={"is_duplicate": True})
        assert p.compute_score(normal) > p.compute_score(dup)


# ===================================================================
# Lifecycle tests
# ===================================================================


class TestAlertLifecycleManager:
    def test_create_alert_sets_active(self):
        mgr = AlertLifecycleManager()
        alert = _make_alert()
        result = mgr.create_alert(alert)
        assert result.state == AlertState.ACTIVE

    def test_hysteresis_suppresses_duplicate(self):
        """Same alert type for the same actors within cooldown is suppressed."""
        mgr = AlertLifecycleManager(default_cooldown_s=60.0)
        a1 = _make_alert(alert_type="collision_warning", actor_ids=["v1", "v2"])
        a2 = _make_alert(alert_type="collision_warning", actor_ids=["v1", "v2"])

        r1 = mgr.create_alert(a1)
        assert r1.state == AlertState.ACTIVE

        r2 = mgr.create_alert(a2)
        assert r2.state == AlertState.SUPPRESSED

    def test_hysteresis_allows_different_actors(self):
        mgr = AlertLifecycleManager(default_cooldown_s=60.0)
        a1 = _make_alert(alert_type="collision_warning", actor_ids=["v1", "v2"])
        a2 = _make_alert(alert_type="collision_warning", actor_ids=["v3", "v4"])

        mgr.create_alert(a1)
        r2 = mgr.create_alert(a2)
        assert r2.state == AlertState.ACTIVE

    def test_hysteresis_allows_different_type(self):
        mgr = AlertLifecycleManager(default_cooldown_s=60.0)
        a1 = _make_alert(alert_type="collision_warning", actor_ids=["v1"])
        a2 = _make_alert(alert_type="pothole", actor_ids=["v1"])

        mgr.create_alert(a1)
        r2 = mgr.create_alert(a2)
        assert r2.state == AlertState.ACTIVE

    def test_valid_state_transitions(self):
        mgr = AlertLifecycleManager()
        alert = mgr.create_alert(_make_alert())
        aid = alert.alert_id

        ack = mgr.update_alert(aid, {"state": AlertState.ACKNOWLEDGED})
        assert ack.state == AlertState.ACKNOWLEDGED

        resolved = mgr.resolve_alert(aid, "threat passed")
        assert resolved.state == AlertState.RESOLVED

    def test_invalid_state_transition_raises(self):
        mgr = AlertLifecycleManager()
        alert = mgr.create_alert(_make_alert())
        aid = alert.alert_id

        mgr.resolve_alert(aid, "done")
        # Resolved → ACTIVE is not valid.
        with pytest.raises(ValueError, match="Invalid transition"):
            mgr.update_alert(aid, {"state": AlertState.ACTIVE})

    def test_expire_stale_via_ttl(self):
        mgr = AlertLifecycleManager()
        alert = _make_alert(ttl_s=0)
        # Manually set created_at in the past
        alert = alert.model_copy(update={"created_at": datetime.now(UTC) - timedelta(seconds=5)})
        created = mgr.create_alert(alert)
        expired = mgr.expire_stale()
        assert created.alert_id in expired

    def test_expire_stale_via_expires_at(self):
        mgr = AlertLifecycleManager()
        past = datetime.now(UTC) - timedelta(seconds=5)
        alert = _make_alert(expires_at=past)
        created = mgr.create_alert(alert)
        expired = mgr.expire_stale()
        assert created.alert_id in expired

    def test_is_suppressed(self):
        mgr = AlertLifecycleManager(default_cooldown_s=60.0)
        assert not mgr.is_suppressed("collision_warning", ["v1"])
        mgr.create_alert(_make_alert(alert_type="collision_warning", actor_ids=["v1"]))
        assert mgr.is_suppressed("collision_warning", ["v1"])

    def test_created_count_tracks_non_suppressed(self):
        mgr = AlertLifecycleManager(default_cooldown_s=60.0)
        mgr.create_alert(_make_alert(alert_type="x", actor_ids=["a"]))
        mgr.create_alert(_make_alert(alert_type="x", actor_ids=["a"]))  # suppressed
        assert mgr.created_count == 1


# ===================================================================
# Audience resolver tests
# ===================================================================


class TestAudienceResolver:
    def test_direct_participants_always_included(self):
        resolver = AudienceResolver()
        alert = _make_alert(actor_ids=["v1", "v2"])
        actors: dict = {}
        result = resolver.resolve_audience(alert, actors)
        assert "v1" in result
        assert "v2" in result

    def test_excludes_actors_on_different_segment(self):
        """Actor on a different road segment is excluded."""
        resolver = AudienceResolver()
        alert = _make_alert(
            machine_reasoning={"road_segment_id": "seg_A"},
            position=GeoPoint(lat=12.97, lon=77.59),
        )
        actors = {
            "v_same": {
                "road_segment_id": "seg_A",
                "position": GeoPoint(lat=12.971, lon=77.591),
            },
            "v_different": {
                "road_segment_id": "seg_B",
                "position": GeoPoint(lat=12.971, lon=77.591),
            },
        }
        result = resolver.resolve_audience(alert, actors)
        assert "v_same" in result
        assert "v_different" not in result

    def test_includes_actor_on_route(self):
        """Actor whose route includes the hazard segment is included."""
        resolver = AudienceResolver()
        alert = _make_alert(
            machine_reasoning={"road_segment_id": "seg_A"},
            position=GeoPoint(lat=12.97, lon=77.59),
        )
        actors = {
            "v_route": {
                "road_segment_id": "seg_C",
                "route_segments": ["seg_C", "seg_A", "seg_D"],
                "position": GeoPoint(lat=12.971, lon=77.591),
            },
        }
        result = resolver.resolve_audience(alert, actors)
        assert "v_route" in result

    def test_excludes_distant_actors(self):
        """Actor outside radius is excluded even if on the same segment."""
        resolver = AudienceResolver(radius_m=100)
        alert = _make_alert(
            position=GeoPoint(lat=12.97, lon=77.59),
        )
        actors = {
            "v_far": {
                "position": GeoPoint(lat=13.0, lon=77.6),  # ~3.5 km away
            },
        }
        result = resolver.resolve_audience(alert, actors)
        assert "v_far" not in result

    def test_includes_nearby_actors(self):
        resolver = AudienceResolver(radius_m=500)
        alert = _make_alert(
            position=GeoPoint(lat=12.97, lon=77.59),
        )
        actors = {
            "v_near": {
                "position": GeoPoint(lat=12.9705, lon=77.5905),  # ~70 m away
            },
        }
        result = resolver.resolve_audience(alert, actors)
        assert "v_near" in result


# ===================================================================
# Store tests
# ===================================================================


class TestAlertStore:
    def test_add_and_get(self):
        s = AlertStore()
        alert = _make_alert()
        s.add(alert)
        assert s.get(alert.alert_id) is not None

    def test_active_count(self):
        s = AlertStore()
        s.add(_make_alert())
        s.add(_make_alert())
        assert s.get_active_count() == 2

    def test_query_by_alert_type(self):
        s = AlertStore()
        s.add(_make_alert(alert_type="pothole"))
        s.add(_make_alert(alert_type="collision_warning"))
        result = s.query(alert_type="pothole")
        assert len(result) == 1
        assert result[0].alert_type == "pothole"

    def test_query_by_actor_id(self):
        s = AlertStore()
        s.add(_make_alert(actor_ids=["v1", "v2"]))
        s.add(_make_alert(actor_ids=["v3"]))
        result = s.query(actor_id="v1")
        assert len(result) == 1

    def test_query_by_bbox(self):
        s = AlertStore()
        inside = _make_alert(position=GeoPoint(lat=12.97, lon=77.59))
        outside = _make_alert(position=GeoPoint(lat=20.0, lon=80.0))
        s.add(inside)
        s.add(outside)
        result = s.query(bbox=(12.0, 77.0, 13.0, 78.0))
        assert len(result) == 1
        assert result[0].alert_id == inside.alert_id

    def test_query_by_priority(self):
        s = AlertStore()
        s.add(_make_alert(priority=AlertPriority.CRITICAL))
        s.add(_make_alert(priority=AlertPriority.LOW))
        result = s.query(priority=AlertPriority.CRITICAL)
        assert len(result) == 1

    def test_resolved_alerts_move_to_history(self):
        s = AlertStore()
        alert = _make_alert()
        s.add(alert)
        assert s.get_active_count() == 1

        resolved = alert.model_copy(update={"state": AlertState.RESOLVED})
        s.update(resolved)
        assert s.get_active_count() == 0
        history = s.get_history()
        assert len(history) == 1
        assert history[0].alert_id == alert.alert_id

    def test_history_is_bounded(self):
        s = AlertStore(history_limit=5)
        for _ in range(10):
            a = _make_alert()
            a = a.model_copy(update={"state": AlertState.EXPIRED})
            s.add(a)
        assert len(s.get_history(limit=100)) == 5

    def test_get_from_history(self):
        s = AlertStore()
        alert = _make_alert()
        resolved = alert.model_copy(update={"state": AlertState.RESOLVED})
        s.add(resolved)
        found = s.get(alert.alert_id)
        assert found is not None
        assert found.state == AlertState.RESOLVED

    def test_query_limit(self):
        s = AlertStore()
        for _ in range(20):
            s.add(_make_alert())
        result = s.query(limit=5)
        assert len(result) == 5


# ===================================================================
# API / WebSocket tests
# ===================================================================


class TestAlertAPI:
    """Integration tests for REST endpoints and WebSocket using TestClient."""

    @pytest.fixture(autouse=True)
    def _reset_state(self):
        """Reset shared singletons between tests."""
        from services.alerts.marga_alerts import api as api_module

        api_module.store = AlertStore()
        api_module.lifecycle = AlertLifecycleManager()
        api_module.prioritizer = AlertPrioritizer()
        yield

    @pytest.fixture
    def client(self):
        from services.alerts.marga_alerts.api import router

        app = FastAPI()
        app.include_router(router)
        return TestClient(app)

    def test_health(self, client: TestClient):
        resp = client.get("/v1/alerts/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["active_alerts"] == 0

    def test_create_and_get_alert(self, client: TestClient):
        payload = {
            "alert_type": "collision_warning",
            "priority": "CRITICAL",
            "title": "Possible collision ahead",
            "description": "Two vehicles converging",
            "confidence": 0.85,
            "position": {"lat": 12.97, "lon": 77.59},
            "affected_actor_ids": ["v1", "v2"],
        }
        resp = client.post("/v1/alerts", json=payload)
        assert resp.status_code == 201
        alert_data = resp.json()
        alert_id = alert_data["alert_id"]

        resp2 = client.get(f"/v1/alerts/{alert_id}")
        assert resp2.status_code == 200
        assert resp2.json()["alert_type"] == "collision_warning"

    def test_list_alerts_with_filters(self, client: TestClient):
        client.post(
            "/v1/alerts",
            json={
                "alert_type": "pothole",
                "priority": "LOW",
                "title": "Pothole",
                "description": "Road damage",
                "confidence": 0.7,
            },
        )
        client.post(
            "/v1/alerts",
            json={
                "alert_type": "collision_warning",
                "priority": "CRITICAL",
                "title": "Collision",
                "description": "Risk",
                "confidence": 0.9,
            },
        )

        resp = client.get("/v1/alerts?alert_type=pothole")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["alert_type"] == "pothole"

    def test_patch_alert_acknowledge(self, client: TestClient):
        resp = client.post(
            "/v1/alerts",
            json={
                "alert_type": "collision_warning",
                "priority": "HIGH",
                "title": "Alert",
                "description": "Desc",
                "confidence": 0.8,
            },
        )
        alert_id = resp.json()["alert_id"]

        resp2 = client.patch(f"/v1/alerts/{alert_id}", json={"state": "ACKNOWLEDGED"})
        assert resp2.status_code == 200
        assert resp2.json()["state"] == "ACKNOWLEDGED"

    def test_patch_alert_resolve(self, client: TestClient):
        resp = client.post(
            "/v1/alerts",
            json={
                "alert_type": "collision_warning",
                "priority": "HIGH",
                "title": "Alert",
                "description": "Desc",
                "confidence": 0.8,
            },
        )
        alert_id = resp.json()["alert_id"]

        resp2 = client.patch(
            f"/v1/alerts/{alert_id}",
            json={
                "state": "RESOLVED",
                "resolution_reason": "Threat passed",
            },
        )
        assert resp2.status_code == 200
        assert resp2.json()["state"] == "RESOLVED"

    def test_get_nonexistent_alert_404(self, client: TestClient):
        resp = client.get(f"/v1/alerts/{uuid4()}")
        assert resp.status_code == 404

    def test_websocket_receives_alert_event(self, client: TestClient):
        """WebSocket client receives alert.issued when a new alert is created."""
        with client.websocket_connect("/v1/stream/alerts") as ws:
            # Create an alert via REST — should trigger broadcast.
            client.post(
                "/v1/alerts",
                json={
                    "alert_type": "collision_warning",
                    "priority": "CRITICAL",
                    "title": "Collision",
                    "description": "Two vehicles",
                    "confidence": 0.9,
                },
            )
            # The broadcast happens in the same event loop iteration as
            # the POST handler, so the message should be available.
            data = ws.receive_json()
            assert data["event"] == "alert.issued"
            assert data["alert"]["alert_type"] == "collision_warning"
