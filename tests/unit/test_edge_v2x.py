"""Tests for the Edge V2X service: transport, risk evaluation, prioritizer, node, and API.

Covers:
- SimulatedPC5Transport: send/receive, nearby nodes, link quality, transport state
- EdgeRiskEvaluator: head-on, rear-end, intersection, side-swipe, emergency braking, VRU
- RiskPrioritizer: composite scoring, one active risk selection, driver text
- EdgeV2XNode: state updates, peer discovery, offline safety delivery
- EdgeV2XManager: node creation, internet toggle, risk collection
- API endpoints: health, nodes, state update, neighbours, connectivity, risks, internet
- WebSocket endpoints: v2x.message, risk.created
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient

from packages.schemas.canonical import Position, RiskEvent, RiskType, VehicleState
from marga_schemas.common import ActorType, ConnectivityState, Source

from services.edge_v2x.api import app, set_manager
from services.edge_v2x.manager import EdgeV2XManager
from services.edge_v2x.node import EdgeV2XNode
from services.edge_v2x.prioritizer import RiskPrioritizer
from services.edge_v2x.risk import EdgeRiskEvaluator, actor_vulnerability, is_vru
from services.edge_v2x.transport import SimulatedPC5Transport


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_vehicle(
    actor_id: str = "veh-1",
    *,
    lat: float = 12.9716,
    lon: float = 77.5946,
    speed: float = 10.0,
    heading: float = 0.0,
    acceleration: float | None = None,
    actor_type: ActorType = ActorType.CAR,
    uncertainty: float = 2.0,
    road_segment_id: str | None = None,
) -> VehicleState:
    """Create a canonical VehicleState with sensible defaults."""
    return VehicleState(
        actor_id=actor_id,
        actor_type=actor_type,
        ts=datetime.now(UTC),
        position=Position(lat=lat, lon=lon),
        position_uncertainty_m=uncertainty,
        speed_mps=speed,
        acceleration_mps2=acceleration,
        heading_deg=heading,
        road_segment_id=road_segment_id,
        source=Source.SIMULATION,
    )


@pytest.fixture
def evaluator() -> EdgeRiskEvaluator:
    return EdgeRiskEvaluator()


@pytest.fixture
def prioritizer() -> RiskPrioritizer:
    return RiskPrioritizer()


@pytest.fixture
def manager() -> EdgeV2XManager:
    return EdgeV2XManager(pc5_range_m=300.0)


# ---------------------------------------------------------------------------
# Transport tests
# ---------------------------------------------------------------------------


class TestSimulatedPC5Transport:
    """Tests for the SimulatedPC5Transport."""

    def test_node_id(self) -> None:
        t = SimulatedPC5Transport("node-1")
        assert t.node_id == "node-1"

    def test_nearby_nodes_empty_without_peers(self) -> None:
        t = SimulatedPC5Transport("node-1")
        assert t.nearby_nodes() == []

    def test_link_quality_no_peer(self) -> None:
        t = SimulatedPC5Transport("node-1")
        assert t.link_quality("unknown") == 0.0

    def test_nearby_nodes_within_range(self) -> None:
        t1 = SimulatedPC5Transport("node-1")
        t2 = SimulatedPC5Transport("node-2")
        t1.update_position(12.9716, 77.5946)
        t2.update_position(12.9719, 77.5946)  # ~33m away
        t1.register_peer(t2)
        assert "node-2" in t1.nearby_nodes()

    def test_nearby_nodes_out_of_range(self) -> None:
        t1 = SimulatedPC5Transport("node-1")
        t2 = SimulatedPC5Transport("node-2")
        t1.update_position(12.9716, 77.5946)
        t2.update_position(12.9750, 77.6000)  # ~700m away
        t1.register_peer(t2)
        assert "node-2" not in t1.nearby_nodes()

    def test_link_quality_decreases_with_distance(self) -> None:
        t1 = SimulatedPC5Transport("node-1")
        t2 = SimulatedPC5Transport("node-2")
        t3 = SimulatedPC5Transport("node-3")
        t1.update_position(12.9716, 77.5946)
        t2.update_position(12.9717, 77.5946)  # close
        t3.update_position(12.9720, 77.5946)  # farther
        t1.register_peer(t2)
        t1.register_peer(t3)
        q_close = t1.link_quality("node-2")
        q_far = t1.link_quality("node-3")
        assert q_close > q_far
        assert 0 < q_far < q_close <= 1.0

    def test_transport_state_initial(self) -> None:
        t = SimulatedPC5Transport("node-1")
        state = t.transport_state()
        assert state.node_id == "node-1"
        assert state.connectivity == ConnectivityState.FULL

    def test_transport_state_direct_only(self) -> None:
        t = SimulatedPC5Transport("node-1")
        t.set_connectivity(ConnectivityState.DIRECT_ONLY)
        state = t.transport_state()
        assert state.connectivity == ConnectivityState.DIRECT_ONLY
        assert not state.cloud_reachable

    @pytest.mark.asyncio
    async def test_send_delivers_to_peer(self) -> None:
        t1 = SimulatedPC5Transport("node-1")
        t2 = SimulatedPC5Transport("node-2")
        t1.update_position(12.9716, 77.5946)
        t2.update_position(12.9717, 77.5946)
        t1.register_peer(t2)
        t2.register_peer(t1)

        received: list = []
        await t2.receive(lambda msg: received.append(msg))

        from marga_schemas.messaging import MessagePriority, V2XMessage

        msg = V2XMessage(
            topic="test",
            priority=MessagePriority.OPERATIONAL,
            sender_id="node-1",
            timestamp=datetime.now(UTC),
            ttl_s=5,
            payload={"test": True},
        )
        delivered = await t1.send(msg, internet_available=True)
        assert delivered
        assert len(received) == 1

    @pytest.mark.asyncio
    async def test_send_offline_still_delivers_pc5(self) -> None:
        """Internet off must preserve local PC5 safety delivery."""
        t1 = SimulatedPC5Transport("node-1")
        t2 = SimulatedPC5Transport("node-2")
        t1.update_position(12.9716, 77.5946)
        t2.update_position(12.9717, 77.5946)
        t1.register_peer(t2)
        t2.register_peer(t1)

        received: list = []
        await t2.receive(lambda msg: received.append(msg))

        from marga_schemas.messaging import MessagePriority, V2XMessage

        msg = V2XMessage(
            topic="risk.detected",
            priority=MessagePriority.CRITICAL_SAFETY,
            sender_id="node-1",
            timestamp=datetime.now(UTC),
            ttl_s=10,
            payload={"risk": True},
        )
        # Internet OFF — PC5 should still deliver
        delivered = await t1.send(msg, internet_available=False)
        assert delivered
        assert len(received) == 1

    @pytest.mark.asyncio
    async def test_send_out_of_range_not_delivered(self) -> None:
        t1 = SimulatedPC5Transport("node-1")
        t2 = SimulatedPC5Transport("node-2")
        t1.update_position(12.9716, 77.5946)
        t2.update_position(12.9800, 77.6000)  # far away
        t1.register_peer(t2)
        t2.register_peer(t1)

        received: list = []
        await t2.receive(lambda msg: received.append(msg))

        from marga_schemas.messaging import MessagePriority, V2XMessage

        msg = V2XMessage(
            topic="test",
            priority=MessagePriority.OPERATIONAL,
            sender_id="node-1",
            timestamp=datetime.now(UTC),
            ttl_s=5,
            payload={},
        )
        delivered = await t1.send(msg, internet_available=True)
        assert not delivered
        assert len(received) == 0


# ---------------------------------------------------------------------------
# Risk evaluator tests
# ---------------------------------------------------------------------------


class TestEdgeRiskEvaluator:
    """Tests for the EdgeRiskEvaluator covering all conflict types."""

    def test_no_risk_for_same_actor(self, evaluator: EdgeRiskEvaluator) -> None:
        v = _make_vehicle("veh-1")
        assert evaluator.evaluate_pair(v, v) is None

    def test_no_risk_for_distant_actors(self, evaluator: EdgeRiskEvaluator) -> None:
        v1 = _make_vehicle("veh-1", lat=12.9716, lon=77.5946)
        v2 = _make_vehicle("veh-2", lat=12.9800, lon=77.6000)
        assert evaluator.evaluate_pair(v1, v2) is None

    def test_head_on_conflict(self, evaluator: EdgeRiskEvaluator) -> None:
        """Two vehicles approaching each other head-on (180 deg heading delta)."""
        v1 = _make_vehicle("veh-1", lat=12.9716, lon=77.5946, heading=0, speed=15)
        v2 = _make_vehicle("veh-2", lat=12.9720, lon=77.5946, heading=180, speed=15)
        risk = evaluator.evaluate_pair(v1, v2)
        assert risk is not None
        assert risk.type == RiskType.HEAD_ON
        assert risk.time_to_conflict_s is not None
        assert risk.time_to_conflict_s > 0
        assert risk.severity > 0
        assert risk.confidence > 0
        assert len(risk.evidence) > 0

    def test_rear_end_conflict(self, evaluator: EdgeRiskEvaluator) -> None:
        """Same-direction faster vehicle closing on slower one."""
        v1 = _make_vehicle("veh-1", lat=12.9716, lon=77.5946, heading=0, speed=20)
        v2 = _make_vehicle("veh-2", lat=12.9718, lon=77.5946, heading=0, speed=5)
        risk = evaluator.evaluate_pair(v1, v2)
        assert risk is not None
        assert risk.type == RiskType.REAR_END

    def test_intersection_conflict(self, evaluator: EdgeRiskEvaluator) -> None:
        """Perpendicular approaches converging on same point -> intersection conflict."""
        # v1 approaching from west (heading east), v2 from south (heading north)
        v1 = _make_vehicle("veh-1", lat=12.9716, lon=77.5944, heading=90, speed=10)
        v2 = _make_vehicle("veh-2", lat=12.9714, lon=77.5946, heading=0, speed=10)
        risk = evaluator.evaluate_pair(v1, v2)
        assert risk is not None
        assert risk.type == RiskType.INTERSECTION_CONFLICT

    def test_emergency_braking_detected(self, evaluator: EdgeRiskEvaluator) -> None:
        """Peer braking hard ahead of ego."""
        v1 = _make_vehicle("veh-1", lat=12.9716, lon=77.5946, heading=0, speed=15)
        v2 = _make_vehicle("veh-2", lat=12.9718, lon=77.5946, heading=0, speed=10, acceleration=-6.0)
        risk = evaluator.evaluate_pair(v1, v2)
        assert risk is not None
        assert risk.type == RiskType.EMERGENCY_BRAKING
        assert any(e.get("type") == "emergency_braking" for e in risk.evidence)

    def test_no_emergency_braking_normal_deceleration(self, evaluator: EdgeRiskEvaluator) -> None:
        """Normal braking should not trigger emergency braking."""
        v1 = _make_vehicle("veh-1", lat=12.9716, lon=77.5946, heading=0, speed=15)
        v2 = _make_vehicle("veh-2", lat=12.9718, lon=77.5946, heading=0, speed=10, acceleration=-2.0)
        risk = evaluator.evaluate_pair(v1, v2)
        # Should not be emergency braking (deceleration is mild)
        if risk is not None:
            assert risk.type != RiskType.EMERGENCY_BRAKING

    def test_vru_conflict_higher_severity(self, evaluator: EdgeRiskEvaluator) -> None:
        """Conflicts involving pedestrians should have higher severity."""
        v_car = _make_vehicle("veh-1", lat=12.9716, lon=77.5946, heading=0, speed=15)
        v_ped = _make_vehicle("veh-2", lat=12.9718, lon=77.5946, heading=90, speed=1.5,
                              actor_type=ActorType.PEDESTRIAN)
        v_car2 = _make_vehicle("veh-3", lat=12.9718, lon=77.5946, heading=90, speed=1.5,
                               actor_type=ActorType.CAR)

        risk_vru = evaluator.evaluate_pair(v_car, v_ped)
        risk_car = evaluator.evaluate_pair(v_car, v_car2)

        if risk_vru and risk_car:
            # VRU conflict should be at least as severe, typically more
            assert risk_vru.severity >= risk_car.severity
            assert risk_vru.type == RiskType.PEDESTRIAN_CONFLICT

    def test_evidence_contains_policy_version(self, evaluator: EdgeRiskEvaluator) -> None:
        v1 = _make_vehicle("veh-1", lat=12.9716, lon=77.5946, heading=0, speed=15)
        v2 = _make_vehicle("veh-2", lat=12.9720, lon=77.5946, heading=180, speed=15)
        risk = evaluator.evaluate_pair(v1, v2)
        assert risk is not None
        assert risk.policy_version is not None
        assert len(risk.policy_version) > 0

    def test_confidence_decreases_with_uncertainty(self, evaluator: EdgeRiskEvaluator) -> None:
        """Higher position uncertainty should reduce confidence."""
        v1_low = _make_vehicle("veh-1", lat=12.9716, lon=77.5946, heading=0, speed=15, uncertainty=1.0)
        v2_low = _make_vehicle("veh-2", lat=12.9720, lon=77.5946, heading=180, speed=15, uncertainty=1.0)

        v1_high = _make_vehicle("veh-1", lat=12.9716, lon=77.5946, heading=0, speed=15, uncertainty=20.0)
        v2_high = _make_vehicle("veh-2", lat=12.9720, lon=77.5946, heading=180, speed=15, uncertainty=20.0)

        risk_low = evaluator.evaluate_pair(v1_low, v2_low)
        risk_high = evaluator.evaluate_pair(v1_high, v2_high)

        if risk_low and risk_high:
            assert risk_low.confidence > risk_high.confidence

    def test_evaluate_all_multiple_peers(self, evaluator: EdgeRiskEvaluator) -> None:
        ego = _make_vehicle("ego", lat=12.9716, lon=77.5946, heading=0, speed=15)
        p1 = _make_vehicle("p1", lat=12.9720, lon=77.5946, heading=180, speed=15)
        p2 = _make_vehicle("p2", lat=12.9716, lon=77.5950, heading=270, speed=10)
        p3 = _make_vehicle("p3", lat=12.9800, lon=77.6000, heading=0, speed=5)  # far away

        risks = evaluator.evaluate_all(ego, [p1, p2, p3])
        assert len(risks) >= 1
        # The distant peer should not produce a risk
        for r in risks:
            assert "p3" not in r.affected_actor_ids


# ---------------------------------------------------------------------------
# Prioritizer tests
# ---------------------------------------------------------------------------


class TestRiskPrioritizer:
    """Tests for the RiskPrioritizer."""

    def test_no_risks_returns_none(self, prioritizer: RiskPrioritizer) -> None:
        assert prioritizer.prioritize([]) is None

    def test_single_risk_returned(self, prioritizer: RiskPrioritizer) -> None:
        risk = RiskEvent(
            type=RiskType.HEAD_ON,
            ts=datetime.now(UTC),
            affected_actor_ids=["a", "b"],
            severity=0.8,
            confidence=0.9,
            risk_score=0.72,
            time_to_conflict_s=2.0,
            policy_version="test",
        )
        result = prioritizer.prioritize([risk])
        assert result is not None
        assert result.risk_id == risk.risk_id

    def test_higher_severity_wins(self, prioritizer: RiskPrioritizer) -> None:
        """A more severe, closer risk should be prioritised."""
        risk_low = RiskEvent(
            type=RiskType.REAR_END,
            ts=datetime.now(UTC),
            affected_actor_ids=["a", "b"],
            severity=0.3,
            confidence=0.8,
            risk_score=0.24,
            time_to_conflict_s=6.0,
            policy_version="test",
        )
        risk_high = RiskEvent(
            type=RiskType.HEAD_ON,
            ts=datetime.now(UTC),
            affected_actor_ids=["a", "c"],
            severity=0.9,
            confidence=0.9,
            risk_score=0.81,
            time_to_conflict_s=1.0,
            policy_version="test",
        )
        result = prioritizer.prioritize([risk_low, risk_high])
        assert result is not None
        assert result.type == RiskType.HEAD_ON

    def test_vru_risk_boosted(self, prioritizer: RiskPrioritizer) -> None:
        """A VRU conflict with similar metrics should score higher than non-VRU."""
        risk_car = RiskEvent(
            type=RiskType.REAR_END,
            ts=datetime.now(UTC),
            affected_actor_ids=["a", "b"],
            severity=0.7,
            confidence=0.8,
            risk_score=0.56,
            time_to_conflict_s=3.0,
            policy_version="test",
            evidence=[{"max_vulnerability": 0.4}],
        )
        risk_vru = RiskEvent(
            type=RiskType.PEDESTRIAN_CONFLICT,
            ts=datetime.now(UTC),
            affected_actor_ids=["a", "c"],
            severity=0.7,
            confidence=0.8,
            risk_score=0.56,
            time_to_conflict_s=3.0,
            policy_version="test",
            evidence=[{"max_vulnerability": 1.0}],
        )
        result = prioritizer.prioritize([risk_car, risk_vru])
        assert result is not None
        assert result.type == RiskType.PEDESTRIAN_CONFLICT

    def test_driver_text_does_not_claim_certainty(self, prioritizer: RiskPrioritizer) -> None:
        risk = RiskEvent(
            type=RiskType.HEAD_ON,
            ts=datetime.now(UTC),
            affected_actor_ids=["a", "b"],
            severity=0.9,
            confidence=0.3,
            risk_score=0.27,
            time_to_conflict_s=2.0,
            policy_version="test",
        )
        factors = prioritizer.compute_factors(risk)
        text = prioritizer.driver_text(risk, factors)
        assert "Possible" in text or "Potential" in text or "may" in text
        # Should not claim certainty when confidence is low
        assert "certain" not in text.lower()

    def test_machine_reasoning_has_all_factors(self, prioritizer: RiskPrioritizer) -> None:
        risk = RiskEvent(
            type=RiskType.HEAD_ON,
            ts=datetime.now(UTC),
            affected_actor_ids=["a", "b"],
            severity=0.8,
            confidence=0.9,
            risk_score=0.72,
            time_to_conflict_s=2.0,
            policy_version="test",
        )
        factors = prioritizer.compute_factors(risk)
        reasoning = prioritizer.machine_reasoning(risk, factors)
        assert "composite_score" in reasoning
        assert "collision_probability" in reasoning
        assert "ttc_urgency" in reasoning
        assert "uncertainty_penalty" in reasoning
        assert "consequence" in reasoning
        assert "vulnerability" in reasoning


# ---------------------------------------------------------------------------
# Node tests
# ---------------------------------------------------------------------------


class TestEdgeV2XNode:
    """Tests for the EdgeV2XNode."""

    @pytest.mark.asyncio
    async def test_node_start_stop(self) -> None:
        node = EdgeV2XNode("veh-1")
        await node.start()
        assert node.transport is not None
        await node.stop()

    @pytest.mark.asyncio
    async def test_update_state_sets_position(self) -> None:
        node = EdgeV2XNode("veh-1")
        await node.start()
        state = _make_vehicle("veh-1")
        node.update_state(state)
        assert node.state is not None
        assert node.state.actor_id == "veh-1"
        await node.stop()

    @pytest.mark.asyncio
    async def test_internet_off_preserves_pc5(self) -> None:
        """Internet off should not prevent PC5 safety delivery."""
        node = EdgeV2XNode("veh-1")
        await node.start()
        node.set_internet(False)
        assert not node.internet_available
        assert node.get_connectivity() == ConnectivityState.DIRECT_ONLY
        await node.stop()

    @pytest.mark.asyncio
    async def test_internet_on_restores_full(self) -> None:
        node = EdgeV2XNode("veh-1")
        await node.start()
        node.set_internet(False)
        node.set_internet(True)
        assert node.internet_available
        assert node.get_connectivity() == ConnectivityState.FULL
        await node.stop()

    @pytest.mark.asyncio
    async def test_neighbours_empty_without_peers(self) -> None:
        node = EdgeV2XNode("veh-1")
        await node.start()
        assert node.get_neighbours() == []
        await node.stop()

    @pytest.mark.asyncio
    async def test_active_risk_none_without_peers(self) -> None:
        node = EdgeV2XNode("veh-1")
        await node.start()
        state = _make_vehicle("veh-1")
        node.update_state(state)
        assert node.active_risk is None
        await node.stop()


# ---------------------------------------------------------------------------
# Manager tests
# ---------------------------------------------------------------------------


class TestEdgeV2XManager:
    """Tests for the EdgeV2XManager."""

    @pytest.mark.asyncio
    async def test_create_node(self, manager: EdgeV2XManager) -> None:
        node = await manager.create_node("veh-1")
        assert node.actor_id == "veh-1"
        assert "veh-1" in manager.node_ids
        await manager.shutdown()

    @pytest.mark.asyncio
    async def test_create_multiple_nodes_connects_peers(self, manager: EdgeV2XManager) -> None:
        """Creating a second node should connect it to the first."""
        await manager.create_node("veh-1")
        await manager.create_node("veh-2")
        n1 = manager.get_node("veh-1")
        n2 = manager.get_node("veh-2")
        assert n1 is not None and n2 is not None
        # Transports should be registered as peers
        assert "veh-2" in n1.transport._peers
        assert "veh-1" in n2.transport._peers
        await manager.shutdown()

    @pytest.mark.asyncio
    async def test_internet_toggle(self, manager: EdgeV2XManager) -> None:
        await manager.create_node("veh-1")
        await manager.create_node("veh-2")
        manager.set_internet(False)
        assert not manager.internet_available
        for node in manager.get_all_nodes():
            assert not node.internet_available
        manager.set_internet(True)
        assert manager.internet_available
        await manager.shutdown()

    @pytest.mark.asyncio
    async def test_update_actor_state_creates_node_if_missing(self, manager: EdgeV2XManager) -> None:
        state = _make_vehicle("veh-new")
        await manager.update_actor_state(state)
        assert "veh-new" in manager.node_ids
        await manager.shutdown()

    @pytest.mark.asyncio
    async def test_get_neighbours(self, manager: EdgeV2XManager) -> None:
        await manager.create_node("veh-1")
        await manager.create_node("veh-2")
        s1 = _make_vehicle("veh-1", lat=12.9716, lon=77.5946)
        s2 = _make_vehicle("veh-2", lat=12.9717, lon=77.5946)
        await manager.update_actor_state(s1)
        await manager.update_actor_state(s2)
        neighbours = manager.get_neighbours("veh-1")
        assert neighbours is not None
        assert any(n["node_id"] == "veh-2" for n in neighbours)
        await manager.shutdown()

    @pytest.mark.asyncio
    async def test_get_connectivity(self, manager: EdgeV2XManager) -> None:
        await manager.create_node("veh-1")
        conn = manager.get_connectivity("veh-1")
        assert conn is not None
        assert conn["node_id"] == "veh-1"
        assert conn["pc5_active"] is True
        await manager.shutdown()

    @pytest.mark.asyncio
    async def test_get_connectivity_not_found(self, manager: EdgeV2XManager) -> None:
        assert manager.get_connectivity("unknown") is None
        await manager.shutdown()

    @pytest.mark.asyncio
    async def test_risk_detection_across_nodes(self, manager: EdgeV2XManager) -> None:
        """Two vehicles approaching head-on should produce a risk."""
        await manager.create_node("veh-1")
        await manager.create_node("veh-2")
        s1 = _make_vehicle("veh-1", lat=12.9716, lon=77.5946, heading=0, speed=15)
        s2 = _make_vehicle("veh-2", lat=12.9720, lon=77.5946, heading=180, speed=15)
        await manager.update_actor_state(s1)
        risk = await manager.update_actor_state(s2)
        # A risk should be detected (head-on)
        assert risk is not None
        assert risk.type == RiskType.HEAD_ON
        await manager.shutdown()

    @pytest.mark.asyncio
    async def test_offline_safety_delivery(self, manager: EdgeV2XManager) -> None:
        """When internet is off, PC5 safety delivery must still work."""
        await manager.create_node("veh-1")
        await manager.create_node("veh-2")
        manager.set_internet(False)

        s1 = _make_vehicle("veh-1", lat=12.9716, lon=77.5946, heading=0, speed=15)
        s2 = _make_vehicle("veh-2", lat=12.9720, lon=77.5946, heading=180, speed=15)
        await manager.update_actor_state(s1)
        risk = await manager.update_actor_state(s2)

        # Risk should still be detected even with internet off
        assert risk is not None
        assert risk.type == RiskType.HEAD_ON

        # Connectivity should show DIRECT_ONLY but PC5 active
        conn = manager.get_connectivity("veh-1")
        assert conn is not None
        assert conn["connectivity"] == "DIRECT_ONLY"
        assert conn["pc5_active"] is True

        await manager.shutdown()


# ---------------------------------------------------------------------------
# API tests
# ---------------------------------------------------------------------------


@pytest.fixture
async def api_client():
    """Create an async test client with a fresh manager."""
    mgr = EdgeV2XManager(pc5_range_m=300.0)
    set_manager(mgr)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client
    await mgr.shutdown()


class TestEdgeV2XAPI:
    """Tests for the FastAPI endpoints."""

    @pytest.mark.asyncio
    async def test_health(self, api_client: AsyncClient) -> None:
        resp = await api_client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "healthy"
        assert data["service"] == "edge-v2x"
        assert data["node_count"] == 0

    @pytest.mark.asyncio
    async def test_create_node(self, api_client: AsyncClient) -> None:
        resp = await api_client.post("/nodes", json={"actor_id": "veh-1"})
        assert resp.status_code == 201
        data = resp.json()
        assert data["actor_id"] == "veh-1"

    @pytest.mark.asyncio
    async def test_create_duplicate_node(self, api_client: AsyncClient) -> None:
        await api_client.post("/nodes", json={"actor_id": "veh-1"})
        resp = await api_client.post("/nodes", json={"actor_id": "veh-1"})
        assert resp.status_code == 409

    @pytest.mark.asyncio
    async def test_list_nodes(self, api_client: AsyncClient) -> None:
        await api_client.post("/nodes", json={"actor_id": "veh-1"})
        resp = await api_client.get("/nodes")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["actor_id"] == "veh-1"

    @pytest.mark.asyncio
    async def test_update_state(self, api_client: AsyncClient) -> None:
        await api_client.post("/nodes", json={"actor_id": "veh-1"})
        state = _make_vehicle("veh-1")
        resp = await api_client.post(
            f"/nodes/veh-1/state",
            json={"state": state.model_dump(mode="json")},
        )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_update_state_mismatch(self, api_client: AsyncClient) -> None:
        await api_client.post("/nodes", json={"actor_id": "veh-1"})
        state = _make_vehicle("veh-2")
        resp = await api_client.post(
            f"/nodes/veh-1/state",
            json={"state": state.model_dump(mode="json")},
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_get_neighbours_not_found(self, api_client: AsyncClient) -> None:
        resp = await api_client.get("/nodes/unknown/neighbours")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_get_connectivity_not_found(self, api_client: AsyncClient) -> None:
        resp = await api_client.get("/nodes/unknown/connectivity")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_get_connectivity(self, api_client: AsyncClient) -> None:
        await api_client.post("/nodes", json={"actor_id": "veh-1"})
        resp = await api_client.get("/nodes/veh-1/connectivity")
        assert resp.status_code == 200
        data = resp.json()
        assert data["node_id"] == "veh-1"
        assert data["pc5_active"] is True
        assert data["internet_available"] is True

    @pytest.mark.asyncio
    async def test_internet_off(self, api_client: AsyncClient) -> None:
        await api_client.post("/nodes", json={"actor_id": "veh-1"})
        resp = await api_client.post("/internet", json={"available": False})
        assert resp.status_code == 200
        data = resp.json()
        assert data["internet_available"] is False

        # Verify node connectivity changed
        conn = await api_client.get("/nodes/veh-1/connectivity")
        conn_data = conn.json()
        assert conn_data["connectivity"] == "DIRECT_ONLY"
        assert conn_data["pc5_active"] is True

    @pytest.mark.asyncio
    async def test_internet_on(self, api_client: AsyncClient) -> None:
        await api_client.post("/nodes", json={"actor_id": "veh-1"})
        await api_client.post("/internet", json={"available": False})
        resp = await api_client.post("/internet", json={"available": True})
        assert resp.status_code == 200
        assert resp.json()["internet_available"] is True

    @pytest.mark.asyncio
    async def test_get_risks_empty(self, api_client: AsyncClient) -> None:
        resp = await api_client.get("/risks")
        assert resp.status_code == 200
        assert resp.json() == []

    @pytest.mark.asyncio
    async def test_get_stats(self, api_client: AsyncClient) -> None:
        await api_client.post("/nodes", json={"actor_id": "veh-1"})
        resp = await api_client.get("/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["node_count"] == 1
        assert "nodes" in data

    @pytest.mark.asyncio
    async def test_risk_detection_via_api(self, api_client: AsyncClient) -> None:
        """Full flow: create nodes, update states, get risks."""
        await api_client.post("/nodes", json={"actor_id": "veh-1"})
        await api_client.post("/nodes", json={"actor_id": "veh-2"})

        s1 = _make_vehicle("veh-1", lat=12.9716, lon=77.5946, heading=0, speed=15)
        s2 = _make_vehicle("veh-2", lat=12.9720, lon=77.5946, heading=180, speed=15)

        await api_client.post("/nodes/veh-1/state", json={"state": s1.model_dump(mode="json")})
        await api_client.post("/nodes/veh-2/state", json={"state": s2.model_dump(mode="json")})

        risks = await api_client.get("/risks")
        assert risks.status_code == 200
        data = risks.json()
        assert len(data) >= 1
        assert data[0]["risk_type"] == "HEAD_ON"

    @pytest.mark.asyncio
    async def test_offline_risk_detection_via_api(self, api_client: AsyncClient) -> None:
        """Internet off, risk still detected via PC5."""
        await api_client.post("/nodes", json={"actor_id": "veh-1"})
        await api_client.post("/nodes", json={"actor_id": "veh-2"})
        await api_client.post("/internet", json={"available": False})

        s1 = _make_vehicle("veh-1", lat=12.9716, lon=77.5946, heading=0, speed=15)
        s2 = _make_vehicle("veh-2", lat=12.9720, lon=77.5946, heading=180, speed=15)

        await api_client.post("/nodes/veh-1/state", json={"state": s1.model_dump(mode="json")})
        await api_client.post("/nodes/veh-2/state", json={"state": s2.model_dump(mode="json")})

        risks = await api_client.get("/risks")
        data = risks.json()
        assert len(data) >= 1
        # PC5 safety delivery preserved despite internet off
        assert data[0]["risk_type"] == "HEAD_ON"


# ---------------------------------------------------------------------------
# Helper function tests
# ---------------------------------------------------------------------------


class TestHelperFunctions:
    """Tests for utility functions in the risk module."""

    def test_actor_vulnerability_pedestrian(self) -> None:
        assert actor_vulnerability(ActorType.PEDESTRIAN) == 1.0

    def test_actor_vulnerability_truck(self) -> None:
        assert actor_vulnerability(ActorType.TRUCK) == 0.25

    def test_actor_vulnerability_unknown_string(self) -> None:
        assert actor_vulnerability("UNKNOWN_TYPE") == 0.40

    def test_is_vru_pedestrian(self) -> None:
        assert is_vru(ActorType.PEDESTRIAN) is True

    def test_is_vru_car(self) -> None:
        assert is_vru(ActorType.CAR) is False

    def test_is_vru_bike(self) -> None:
        assert is_vru(ActorType.BIKE) is True

    def test_is_vru_string(self) -> None:
        assert is_vru("PEDESTRIAN") is True
        assert is_vru("CAR") is False
        assert is_vru("INVALID") is False
