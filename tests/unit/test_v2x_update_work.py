"""Repeated telemetry must not trigger another network-wide discovery storm."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from marga_schemas.common import Source

from packages.schemas.canonical import Position, VehicleState
from services.edge_v2x.manager import EdgeV2XManager


def state(actor_id: str, latitude: float = 12.0) -> VehicleState:
    return VehicleState(
        actor_id=actor_id, ts=datetime.now(UTC),
        position=Position(lat=latitude, lon=77.0),
        speed_mps=5, heading_deg=0, position_uncertainty_m=2,
        source=Source.SIMULATION,
    )


@pytest.mark.asyncio
async def test_discovery_only_on_range_entry() -> None:
    manager = EdgeV2XManager()
    try:
        await manager.update_actor_state(state("a"))
        await manager.update_actor_state(state("b", 12.0001))
        peer = manager.get_node("a")
        assert peer is not None
        peer.broadcast_state = AsyncMock(wraps=peer.broadcast_state)
        await manager.update_actor_state(state("b", 12.0002))
        peer.broadcast_state.assert_not_called()
        assert "b" in peer._peers
        await manager.update_actor_state(state("b", 12.01))
        assert "b" not in peer._peers
        await manager.update_actor_state(state("b", 12.0001))
        peer.broadcast_state.assert_awaited_once()
        assert "b" in peer._peers
    finally:
        await manager.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("explicit", [True, False])
async def test_world_departure_retires_edge_node(monkeypatch: pytest.MonkeyPatch, explicit: bool) -> None:
    from services.gateway import v2x_bridge, world_state

    manager = EdgeV2XManager()
    monkeypatch.setattr(v2x_bridge, "manager", manager)
    monkeypatch.setattr(world_state, "_entities", {})
    monkeypatch.setattr(world_state, "_last_seen", {})
    monkeypatch.setattr(world_state, "_notify", lambda delta: None, raising=False)
    actor = state("departing")
    try:
        await manager.update_actor_state(actor)
        world_state._store_model("vehicle", actor.actor_id, actor)
        if explicit:
            await world_state.retire_actor(actor.actor_id)
        else:
            world_state._last_seen[("vehicle", actor.actor_id)] -= 60
            await world_state.sweep_stale_entities()
        assert manager.node_ids == []
    finally:
        await manager.shutdown()


@pytest.mark.asyncio
async def test_retirement_cleans_mesh_and_throttle(monkeypatch: pytest.MonkeyPatch) -> None:
    from services.gateway import v2x_bridge

    manager = EdgeV2XManager()
    monkeypatch.setattr(v2x_bridge, "manager", manager)
    monkeypatch.setattr(v2x_bridge, "_last_observation_at", {"a": 1.0})
    try:
        await manager.update_actor_state(state("a"))
        await manager.update_actor_state(state("b", 12.0001))
        await v2x_bridge.retire_actor("a")
        assert manager.node_ids == ["b"]
        assert "a" not in v2x_bridge._last_observation_at
        node = manager.get_node("b")
        assert node is not None
        assert node._peers == {}
        assert node.active_risk is None
        assert node.get_neighbours() == []
    finally:
        await manager.shutdown()
