"""A producer that stops reporting must not leave actors frozen in the world.

Browser adapters retire their actors explicitly, but a closed or reloaded tab
never gets the chance. Without a server-side sweep those actors accumulate
indefinitely and the map fills with vehicles that will never move again.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from packages.schemas.canonical import ActorType, Position, SourceType, VehicleState
from services.gateway import world_state
from services.gateway.world_state import (
    _entities,
    _last_seen,
    ingest_vehicle_state,
    prune_stale_entities,
    sweep_stale_entities,
)


def _vehicle(actor_id: str) -> VehicleState:
    return VehicleState(
        actor_id=actor_id,
        actor_type=ActorType.CAR,
        ts=datetime.now(UTC),
        position=Position(lat=12.9716, lon=77.5946),
        position_uncertainty_m=2.0,
        speed_mps=8.0,
        heading_deg=90.0,
        source=SourceType.SIMULATION,
    )


@pytest.fixture(autouse=True)
def _clean_world() -> None:
    _entities.clear()
    _last_seen.clear()


@pytest.mark.asyncio
async def test_actor_that_stopped_reporting_is_evicted() -> None:
    await ingest_vehicle_state(_vehicle("abandoned-1"), notify_subscribers=False)
    assert ("vehicle", "abandoned-1") in _entities

    # Backdate liveness rather than sleeping: the sweep is defined against the
    # server clock, so the test asserts the rule, not a wall-clock delay.
    _last_seen[("vehicle", "abandoned-1")] -= 60.0
    assert await sweep_stale_entities(ttl_s=10.0) == 1
    assert ("vehicle", "abandoned-1") not in _entities
    assert ("vehicle", "abandoned-1") not in _last_seen


@pytest.mark.asyncio
async def test_a_still_reporting_actor_is_never_evicted() -> None:
    """A stopped vehicle is not an abandoned one — it must stay on the map."""
    await ingest_vehicle_state(_vehicle("live-1"), notify_subscribers=False)
    _last_seen[("vehicle", "live-1")] -= 60.0
    # A fresh report from the same producer restores liveness.
    await ingest_vehicle_state(_vehicle("live-1"), notify_subscribers=False)

    assert await sweep_stale_entities(ttl_s=10.0) == 0
    assert ("vehicle", "live-1") in _entities


@pytest.mark.asyncio
async def test_eviction_is_broadcast_so_dashboards_drop_the_actor() -> None:
    await ingest_vehicle_state(_vehicle("abandoned-2"), notify_subscribers=False)
    _last_seen[("vehicle", "abandoned-2")] -= 60.0

    seen: list[dict] = []
    world_state._notify = lambda delta: seen.append(delta)  # type: ignore[assignment]
    try:
        await sweep_stale_entities(ttl_s=10.0)
    finally:
        del world_state._notify  # restore the module-level function

    assert seen and seen[0]["deletes"] == [{"entity_type": "vehicle", "entity_id": "abandoned-2"}]
    assert seen[0]["upserts"] == []


def test_hazards_and_risks_are_not_swept() -> None:
    """Hazards carry their own expiry and risks are recomputed every frame."""
    _entities[("hazard", "h-1")] = {"hazard_id": "h-1"}
    _entities[("risk", "r-1")] = {"risk_id": "r-1"}

    assert prune_stale_entities(ttl_s=0.0) == []
    assert ("hazard", "h-1") in _entities
    assert ("risk", "r-1") in _entities
