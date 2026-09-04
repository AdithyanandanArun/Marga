"""Map repository — async database access layer for road network data."""

from __future__ import annotations

import json
from typing import Optional

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import (
    PedestrianCrossingModel,
    RegionModel,
    RoadEdgeModel,
    TrafficSignalModel,
)
from .schema import (
    PedestrianCrossing,
    Position,
    RoadEdge,
    RoadNetwork,
    RoadNode,
    TrafficSignal,
)


class MapRepository:
    """Async repository for reading and writing road network data.

    All methods accept an ``AsyncSession`` injected by FastAPI's dependency
    system; they do **not** manage transaction boundaries (the caller or the
    ``get_db`` dependency does that).
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    async def save_network(self, network: RoadNetwork) -> str:
        """Persist a ``RoadNetwork`` and return the region name.

        Raises ``ValueError`` if a region with the same name already exists.
        Use ``upsert_network`` for idempotent behaviour.
        """
        existing = await self._get_region_model(network.region_name)
        if existing is not None:
            raise ValueError(
                f"Region '{network.region_name}' already exists. "
                "Use upsert_network() to update it."
            )
        await self._insert_network(network)
        return network.region_name

    async def upsert_network(self, network: RoadNetwork) -> str:
        """Idempotent upsert — delete existing region data then re-insert.

        Returns the region name.
        """
        existing = await self._get_region_model(network.region_name)
        if existing is not None:
            # Cascade delete clears edges, signals, crossings
            await self._session.delete(existing)
            await self._session.flush()
        await self._insert_network(network)
        return network.region_name

    async def _insert_network(self, network: RoadNetwork) -> RegionModel:
        region = RegionModel(
            name=network.region_name,
            bbox_json=json.dumps(network.bbox),
            imported_at=network.imported_at,
            schema_version=network.schema_version,
        )
        self._session.add(region)
        await self._session.flush()  # populate region.id

        for edge in network.edges:
            self._session.add(
                RoadEdgeModel(
                    region_id=region.id,
                    edge_id=edge.edge_id,
                    from_node=edge.from_node,
                    to_node=edge.to_node,
                    length_m=edge.length_m,
                    lanes=edge.lanes,
                    speed_limit_mps=edge.speed_limit_mps,
                    road_type=edge.road_type,
                    name=edge.name,
                    geometry_json=json.dumps(
                        [{"lat": p.lat, "lon": p.lon} for p in edge.geometry]
                    ),
                )
            )

        for sig in network.signals:
            self._session.add(
                TrafficSignalModel(
                    region_id=region.id,
                    signal_id=sig.signal_id,
                    node_id=sig.node_id,
                    position_json=json.dumps({"lat": sig.position.lat, "lon": sig.position.lon}),
                    controlled_edges_json=json.dumps(sig.controlled_edges),
                )
            )

        for cross in network.crossings:
            self._session.add(
                PedestrianCrossingModel(
                    region_id=region.id,
                    crossing_id=cross.crossing_id,
                    position_json=json.dumps(
                        {"lat": cross.position.lat, "lon": cross.position.lon}
                    ),
                    edge_id=cross.edge_id,
                )
            )

        await self._session.flush()
        return region

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    async def _get_region_model(self, region_name: str) -> RegionModel | None:
        result = await self._session.execute(
            select(RegionModel).where(RegionModel.name == region_name)
        )
        return result.scalar_one_or_none()

    async def get_network(self, region_name: str) -> Optional[RoadNetwork]:
        """Return the full ``RoadNetwork`` for *region_name*, or ``None``."""
        region = await self._get_region_model(region_name)
        if region is None:
            return None

        edges = await self.get_edges(region_name, limit=100_000, offset=0)
        signals = await self.get_signals(region_name)
        crossings = await self._get_crossings(region_name)

        return RoadNetwork(
            schema_version=region.schema_version,
            region_name=region.name,
            bbox=json.loads(region.bbox_json),
            imported_at=region.imported_at,
            edges=edges,
            nodes=[],  # nodes not stored separately; reconstruct from edges if needed
            signals=signals,
            crossings=crossings,
        )

    async def list_regions(self) -> list[str]:
        """Return a list of all imported region names."""
        result = await self._session.execute(select(RegionModel.name).order_by(RegionModel.name))
        return list(result.scalars().all())

    async def get_edges(
        self, region_name: str, limit: int = 100, offset: int = 0
    ) -> list[RoadEdge]:
        """Return a paginated list of edges for *region_name*."""
        region = await self._get_region_model(region_name)
        if region is None:
            return []

        result = await self._session.execute(
            select(RoadEdgeModel)
            .where(RoadEdgeModel.region_id == region.id)
            .order_by(RoadEdgeModel.id)
            .limit(limit)
            .offset(offset)
        )
        rows = result.scalars().all()
        return [_row_to_edge(row) for row in rows]

    async def get_signals(self, region_name: str) -> list[TrafficSignal]:
        """Return all traffic signals for *region_name*."""
        region = await self._get_region_model(region_name)
        if region is None:
            return []

        result = await self._session.execute(
            select(TrafficSignalModel).where(TrafficSignalModel.region_id == region.id)
        )
        rows = result.scalars().all()
        return [_row_to_signal(row) for row in rows]

    async def _get_crossings(self, region_name: str) -> list[PedestrianCrossing]:
        region = await self._get_region_model(region_name)
        if region is None:
            return []

        result = await self._session.execute(
            select(PedestrianCrossingModel).where(
                PedestrianCrossingModel.region_id == region.id
            )
        )
        rows = result.scalars().all()
        return [_row_to_crossing(row) for row in rows]

    async def get_bbox(self, region_name: str) -> Optional[dict]:
        """Return the bbox dict for *region_name*, or ``None``."""
        region = await self._get_region_model(region_name)
        if region is None:
            return None
        return json.loads(region.bbox_json)


# ---------------------------------------------------------------------------
# Row → Pydantic conversion helpers
# ---------------------------------------------------------------------------

def _row_to_edge(row: RoadEdgeModel) -> RoadEdge:
    geometry_raw = json.loads(row.geometry_json)
    geometry = [Position(lat=p["lat"], lon=p["lon"]) for p in geometry_raw]
    return RoadEdge(
        edge_id=row.edge_id,
        osm_way_id=None,
        from_node=row.from_node,
        to_node=row.to_node,
        length_m=row.length_m,
        lanes=row.lanes,
        speed_limit_mps=row.speed_limit_mps,
        road_type=row.road_type,
        name=row.name,
        geometry=geometry,
    )


def _row_to_signal(row: TrafficSignalModel) -> TrafficSignal:
    pos_raw = json.loads(row.position_json)
    return TrafficSignal(
        signal_id=row.signal_id,
        node_id=row.node_id,
        position=Position(lat=pos_raw["lat"], lon=pos_raw["lon"]),
        controlled_edges=json.loads(row.controlled_edges_json),
    )


def _row_to_crossing(row: PedestrianCrossingModel) -> PedestrianCrossing:
    pos_raw = json.loads(row.position_json)
    return PedestrianCrossing(
        crossing_id=row.crossing_id,
        position=Position(lat=pos_raw["lat"], lon=pos_raw["lon"]),
        edge_id=row.edge_id,
    )
