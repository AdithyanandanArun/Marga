"""Marga Map Service — FastAPI application entry point."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Query

from .database import DBSession, create_tables
from .repository import MapRepository
from .schema import ImportReport, RoadEdge, RoadNetwork, TrafficSignal


# ---------------------------------------------------------------------------
# Lifespan (startup / shutdown)
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):  # type: ignore[type-arg]
    """Run DB table creation on startup."""
    await create_tables()
    yield


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Marga Map Service",
    description=(
        "Stores and serves normalised OSM road networks for the Marga V2X platform."
    ),
    version="0.1.0",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@app.get("/health", tags=["ops"])
async def health() -> dict[str, str]:
    """Liveness probe — always returns 200 OK."""
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Map import
# ---------------------------------------------------------------------------

@app.post("/v1/maps/import", tags=["maps"], status_code=201)
async def import_map(
    network: RoadNetwork,
    session: DBSession,
) -> dict[str, Any]:
    """Persist a RoadNetwork produced by the OSM import CLI.

    Uses an idempotent upsert — re-importing the same region replaces the
    existing data.
    """
    repo = MapRepository(session)
    region_name = await repo.upsert_network(network)
    return {
        "region_name": region_name,
        "edge_count": len(network.edges),
        "signal_count": len(network.signals),
        "crossing_count": len(network.crossings),
    }


# ---------------------------------------------------------------------------
# Region listing
# ---------------------------------------------------------------------------

@app.get("/v1/maps", tags=["maps"])
async def list_regions(session: DBSession) -> list[str]:
    """Return a list of all imported region names."""
    repo = MapRepository(session)
    return await repo.list_regions()


# ---------------------------------------------------------------------------
# Per-region endpoints
# ---------------------------------------------------------------------------

@app.get("/v1/maps/{region_name}", tags=["maps"])
async def get_network(region_name: str, session: DBSession) -> RoadNetwork:
    """Return the full RoadNetwork for a region."""
    repo = MapRepository(session)
    network = await repo.get_network(region_name)
    if network is None:
        raise HTTPException(status_code=404, detail=f"Region '{region_name}' not found.")
    return network


@app.get("/v1/maps/{region_name}/edges", tags=["maps"])
async def get_edges(
    region_name: str,
    session: DBSession,
    limit: Annotated[int, Query(ge=1, le=10_000)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[RoadEdge]:
    """Return paginated road edges for a region."""
    repo = MapRepository(session)
    edges = await repo.get_edges(region_name, limit=limit, offset=offset)
    if not edges and offset == 0:
        # Check whether the region exists at all
        regions = await repo.list_regions()
        if region_name not in regions:
            raise HTTPException(status_code=404, detail=f"Region '{region_name}' not found.")
    return edges


@app.get("/v1/maps/{region_name}/signals", tags=["maps"])
async def get_signals(region_name: str, session: DBSession) -> list[TrafficSignal]:
    """Return all traffic signals for a region."""
    repo = MapRepository(session)
    # Validate region exists
    bbox = await repo.get_bbox(region_name)
    if bbox is None:
        raise HTTPException(status_code=404, detail=f"Region '{region_name}' not found.")
    return await repo.get_signals(region_name)


@app.get("/v1/maps/{region_name}/bbox", tags=["maps"])
async def get_bbox(region_name: str, session: DBSession) -> dict:
    """Return the bounding box for a region."""
    repo = MapRepository(session)
    bbox = await repo.get_bbox(region_name)
    if bbox is None:
        raise HTTPException(status_code=404, detail=f"Region '{region_name}' not found.")
    return bbox
