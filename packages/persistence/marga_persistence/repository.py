"""Data access layer — async repository classes for Marga domain objects."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from marga_persistence.models import (
    AlertRow,
    HazardObservationRow,
    HazardRow,
    SystemAuditEventRow,
)


class HazardRepository:
    """Async CRUD operations for hazards and hazard observations."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def save_hazard(self, hazard: HazardRow) -> HazardRow:
        self._session.add(hazard)
        await self._session.flush()
        return hazard

    async def get_hazard(self, hazard_id: UUID) -> HazardRow | None:
        result = await self._session.execute(
            select(HazardRow).where(HazardRow.hazard_id == hazard_id)
        )
        return result.scalar_one_or_none()

    async def list_hazards(
        self,
        *,
        state: str | None = None,
        bbox: tuple[float, float, float, float] | None = None,
        limit: int = 100,
    ) -> list[HazardRow]:
        """List hazards, optionally filtered by state and bounding box.

        ``bbox`` is ``(min_lon, min_lat, max_lon, max_lat)`` — the standard
        GeoJSON / WMS convention.
        """
        stmt = select(HazardRow)
        if state is not None:
            stmt = stmt.where(HazardRow.state == state)
        if bbox is not None:
            from geoalchemy2.functions import ST_MakeEnvelope, ST_Within

            envelope = ST_MakeEnvelope(bbox[0], bbox[1], bbox[2], bbox[3], 4326)
            stmt = stmt.where(ST_Within(HazardRow.position, envelope))
        stmt = stmt.limit(limit)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def update_hazard(self, hazard: HazardRow) -> HazardRow:
        hazard.updated_at = datetime.now(timezone.utc)
        merged = await self._session.merge(hazard)
        await self._session.flush()
        return merged

    # --- observations ---

    async def save_observation(self, obs: HazardObservationRow) -> HazardObservationRow:
        self._session.add(obs)
        await self._session.flush()
        return obs

    async def get_observations_for_hazard(
        self, hazard_id: UUID
    ) -> list[HazardObservationRow]:
        result = await self._session.execute(
            select(HazardObservationRow).where(
                HazardObservationRow.hazard_id == hazard_id
            )
        )
        return list(result.scalars().all())


class AlertRepository:
    """Async CRUD operations for alerts."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def save_alert(self, alert: AlertRow) -> AlertRow:
        self._session.add(alert)
        await self._session.flush()
        return alert

    async def get_alert(self, alert_id: UUID) -> AlertRow | None:
        result = await self._session.execute(
            select(AlertRow).where(AlertRow.alert_id == alert_id)
        )
        return result.scalar_one_or_none()

    async def list_alerts(
        self,
        *,
        state: str | None = None,
        bbox: tuple[float, float, float, float] | None = None,
        limit: int = 100,
    ) -> list[AlertRow]:
        stmt = select(AlertRow)
        if state is not None:
            stmt = stmt.where(AlertRow.state == state)
        if bbox is not None:
            from geoalchemy2.functions import ST_MakeEnvelope, ST_Within

            envelope = ST_MakeEnvelope(bbox[0], bbox[1], bbox[2], bbox[3], 4326)
            stmt = stmt.where(ST_Within(AlertRow.position, envelope))
        stmt = stmt.limit(limit)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def update_alert(self, alert: AlertRow) -> AlertRow:
        alert.updated_at = datetime.now(timezone.utc)
        merged = await self._session.merge(alert)
        await self._session.flush()
        return merged


class AuditRepository:
    """Append-only repository for system audit events."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def log_event(self, event: SystemAuditEventRow) -> SystemAuditEventRow:
        self._session.add(event)
        await self._session.flush()
        return event

    async def query_events(
        self,
        *,
        event_type: str | None = None,
        source_service: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        trace_id: str | None = None,
        limit: int = 100,
    ) -> list[SystemAuditEventRow]:
        stmt = select(SystemAuditEventRow)
        if event_type is not None:
            stmt = stmt.where(SystemAuditEventRow.event_type == event_type)
        if source_service is not None:
            stmt = stmt.where(SystemAuditEventRow.source_service == source_service)
        if since is not None:
            stmt = stmt.where(SystemAuditEventRow.timestamp >= since)
        if until is not None:
            stmt = stmt.where(SystemAuditEventRow.timestamp <= until)
        if trace_id is not None:
            stmt = stmt.where(SystemAuditEventRow.trace_id == trace_id)
        stmt = stmt.order_by(SystemAuditEventRow.timestamp.desc()).limit(limit)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())
