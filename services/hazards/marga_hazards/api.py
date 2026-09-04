"""FastAPI router for the Hazard Fusion service."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from marga_schemas.common import GeoPoint
from marga_schemas.hazard import Hazard, HazardObservation, HazardState, HazardType

from .fusion import HazardFusionEngine

logger = logging.getLogger(__name__)

# ── Singleton engine (created at import time for simplicity) ──────────
_engine = HazardFusionEngine()

router = APIRouter(prefix="/v1", tags=["hazards"])


# ── Request / Response models ─────────────────────────────────────────


class NegativeEvidenceRequest(BaseModel):
    source_id: str
    position: GeoPoint
    hazard_type: HazardType
    radius_m: float | None = None


class HazardResponse(BaseModel):
    hazard: Hazard
    observation_count: int = 0


class HazardListResponse(BaseModel):
    hazards: list[Hazard]
    total: int


class HealthResponse(BaseModel):
    status: str = "ok"
    active_hazards: int = 0
    utc_now: str = ""


# ── Endpoints ─────────────────────────────────────────────────────────


@router.post("/ingest/hazard-observation", response_model=HazardResponse, status_code=201)
async def ingest_observation(obs: HazardObservation) -> HazardResponse:
    """Ingest a new hazard observation, run fusion, return fused hazard."""
    hazard = _engine.ingest_observation(obs)
    hid = str(hazard.hazard_id)
    obs_count = len(_engine.observation_history.get(hid, []))
    return HazardResponse(hazard=hazard, observation_count=obs_count)


@router.get("/hazards", response_model=HazardListResponse)
async def list_hazards(
    min_lat: Annotated[float | None, Query(ge=-90, le=90)] = None,
    min_lon: Annotated[float | None, Query(ge=-180, le=180)] = None,
    max_lat: Annotated[float | None, Query(ge=-90, le=90)] = None,
    max_lon: Annotated[float | None, Query(ge=-180, le=180)] = None,
) -> HazardListResponse:
    """List active hazards with optional bounding-box filter."""
    bbox: tuple[float, float, float, float] | None = None
    if all(v is not None for v in (min_lat, min_lon, max_lat, max_lon)):
        bbox = (min_lat, min_lon, max_lat, max_lon)  # type: ignore[arg-type]
    hazards = _engine.list_active_hazards(bbox=bbox)
    return HazardListResponse(hazards=hazards, total=len(hazards))


@router.get("/hazards/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """Health check endpoint."""
    return HealthResponse(
        status="ok",
        active_hazards=len(_engine.hazards),
        utc_now=datetime.now(timezone.utc).isoformat(),
    )


@router.get("/hazards/{hazard_id}", response_model=HazardResponse)
async def get_hazard(hazard_id: str) -> HazardResponse:
    """Get a specific hazard with its observation count."""
    hazard = _engine.get_hazard(hazard_id)
    if hazard is None:
        raise HTTPException(status_code=404, detail=f"Hazard {hazard_id} not found")
    obs_count = len(_engine.observation_history.get(hazard_id, []))
    return HazardResponse(hazard=hazard, observation_count=obs_count)


@router.post("/hazards/{hazard_id}/negative-evidence", response_model=HazardListResponse)
async def submit_negative_evidence(
    hazard_id: str,
    body: NegativeEvidenceRequest,
) -> HazardListResponse:
    """Submit negative evidence that a hazard no longer exists."""
    affected = _engine.apply_negative_evidence(
        source_id=body.source_id,
        position=body.position,
        hazard_type=body.hazard_type,
        radius_m=body.radius_m,
    )
    return HazardListResponse(hazards=affected, total=len(affected))


# ── Application factory ──────────────────────────────────────────────


def create_app(*, engine: HazardFusionEngine | None = None) -> FastAPI:
    """Create a FastAPI application wired to the given (or default) fusion engine."""
    global _engine  # noqa: PLW0603
    if engine is not None:
        _engine = engine

    app = FastAPI(
        title="Marga Hazard Fusion Service",
        version="0.1.0",
        description="Cooperative hazard detection and lifecycle management for V2X.",
    )
    app.include_router(router)
    return app


def get_engine() -> HazardFusionEngine:
    """Return the module-level fusion engine (useful for tests)."""
    return _engine


def set_engine(engine: HazardFusionEngine) -> None:
    """Replace the module-level fusion engine (useful for tests)."""
    global _engine  # noqa: PLW0603
    _engine = engine
