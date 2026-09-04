"""Replay proxy — aggregates scenario-service run/event data for the replay view."""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

import httpx
from fastapi import APIRouter, HTTPException, Query

logger = logging.getLogger("marga.gateway.replay")
router = APIRouter(prefix="/v1/replay", tags=["replay"])

_SCENARIO_URL = os.environ.get("SCENARIO_SERVICE_URL", "http://localhost:8001")


@router.get("/runs")
async def list_replay_runs(scenario_id: Optional[str] = Query(None)) -> list[dict[str, Any]]:
    """List all scenario runs available for replay."""
    params: dict[str, str] = {}
    if scenario_id:
        params["scenario_id"] = scenario_id
    async with httpx.AsyncClient(timeout=5.0) as client:
        try:
            resp = await client.get(f"{_SCENARIO_URL}/v1/runs", params=params)
            resp.raise_for_status()
            return resp.json()  # type: ignore[no-any-return]
        except Exception as exc:
            logger.debug("scenario-service unreachable: %s", exc)
            return []


@router.get("/{run_id}/events")
async def get_replay_events(
    run_id: str,
    from_s: float = Query(0.0, description="Start sim time (s)"),
    to_s: Optional[float] = Query(None, description="End sim time (s)"),
    limit: int = Query(5000, le=50_000),
) -> dict[str, Any]:
    """Return recorded canonical events for a run, for ReplayView scrubbing."""
    async with httpx.AsyncClient(timeout=5.0) as client:
        run_resp = await client.get(f"{_SCENARIO_URL}/v1/runs/{run_id}")
        if run_resp.status_code == 404:
            raise HTTPException(status_code=404, detail=f"Run {run_id!r} not found")
        run_resp.raise_for_status()
        run: dict[str, Any] = run_resp.json()
        scenario_id = str(run["scenario_id"])

        params: dict[str, Any] = {"from_s": from_s, "limit": limit}
        if to_s is not None:
            params["to_s"] = to_s

        events_resp = await client.get(
            f"{_SCENARIO_URL}/v1/scenarios/{scenario_id}/runs/{run_id}/events",
            params=params,
        )
        events_resp.raise_for_status()
        data: dict[str, Any] = events_resp.json()
        data["run"] = run
        return data
