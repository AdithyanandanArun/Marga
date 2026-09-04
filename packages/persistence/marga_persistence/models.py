"""SQLAlchemy ORM models for Marga persistence.

All spatial columns use PostGIS Geometry(Point, 4326) via GeoAlchemy2.
When running against SQLite (unit tests) the Geometry columns degrade to
plain strings — the migration and production path always targets PostGIS.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from geoalchemy2 import Geometry
from sqlalchemy import (
    ARRAY,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    """Shared declarative base for all Marga models."""

    pass


class HazardRow(Base):
    __tablename__ = "hazards"

    hazard_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4
    )
    hazard_type: Mapped[str] = mapped_column(String(64), nullable=False)
    position = mapped_column(
        Geometry(geometry_type="POINT", srid=4326), nullable=False
    )
    severity: Mapped[float] = mapped_column(Float, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    first_seen: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    last_seen: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    ttl_s: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False, default="CANDIDATE")
    evidence_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    road_segment_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    observations: Mapped[list["HazardObservationRow"]] = relationship(
        back_populates="hazard", lazy="selectin"
    )


class HazardObservationRow(Base):
    __tablename__ = "hazard_observations"

    observation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4
    )
    hazard_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        nullable=True,
    )
    source_id: Mapped[str] = mapped_column(String(128), nullable=False)
    detector_confidence: Mapped[float] = mapped_column(Float, nullable=False)
    severity_hint: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    position = mapped_column(
        Geometry(geometry_type="POINT", srid=4326), nullable=True
    )
    metadata_: Mapped[dict | None] = mapped_column(
        "metadata", JSONB, nullable=True, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    hazard: Mapped[HazardRow | None] = relationship(back_populates="observations")


class IncidentRow(Base):
    __tablename__ = "incidents"

    incident_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4
    )
    incident_type: Mapped[str] = mapped_column(String(64), nullable=False)
    affected_actor_ids = mapped_column(ARRAY(String), nullable=True)
    position = mapped_column(
        Geometry(geometry_type="POINT", srid=4326), nullable=True
    )
    severity: Mapped[float] = mapped_column(Float, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    evidence = mapped_column(JSONB, nullable=True)


class AlertRow(Base):
    __tablename__ = "alerts"

    alert_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4
    )
    alert_type: Mapped[str] = mapped_column(String(64), nullable=False)
    priority: Mapped[str] = mapped_column(String(32), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False, default="ACTIVE")
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    position = mapped_column(
        Geometry(geometry_type="POINT", srid=4326), nullable=True
    )
    affected_actor_ids = mapped_column(ARRAY(String), nullable=True)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    evidence = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    policy_version: Mapped[str] = mapped_column(
        String(32), nullable=False, default="0.1.0"
    )


class TrustEventRow(Base):
    __tablename__ = "trust_events"

    event_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4
    )
    sender_id: Mapped[str] = mapped_column(String(128), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    detail = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )


class SystemAuditEventRow(Base):
    __tablename__ = "system_audit_events"

    event_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    source_service: Mapped[str] = mapped_column(String(128), nullable=False)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    detail = mapped_column(JSONB, nullable=True)
    trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
