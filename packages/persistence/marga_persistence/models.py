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
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    Uuid,
    event,
    inspect,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class PointGeometry(TypeDecorator):
    """Use PostGIS points in production and plain WKT strings in SQLite tests."""

    impl = Text
    cache_ok = True

    def load_dialect_impl(self, dialect):  # type: ignore[no-untyped-def]
        if dialect.name == "postgresql":
            return dialect.type_descriptor(Geometry(geometry_type="POINT", srid=4326))
        return dialect.type_descriptor(Text())


JsonDocument = JSON().with_variant(JSONB(), "postgresql")
StringArray = JSON().with_variant(ARRAY(String), "postgresql")


class Base(DeclarativeBase):
    """Shared declarative base for all Marga models."""

    pass


@event.listens_for(Base, "init", propagate=True)
def _apply_python_column_defaults(target, args, kwargs):  # type: ignore[no-untyped-def]
    """Expose ORM defaults immediately, as well as when rows are inserted."""
    for column in inspect(target).mapper.columns:
        if column.key in kwargs or column.default is None:
            continue
        value = column.default.arg
        if callable(value):
            try:
                value = value()
            except TypeError:
                # SQLAlchemy wraps context-free defaults with a context argument.
                value = value(None)
        kwargs[column.key] = value


class HazardRow(Base):
    __tablename__ = "hazards"

    hazard_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    hazard_type: Mapped[str] = mapped_column(String(64), nullable=False)
    position = mapped_column(PointGeometry(), nullable=False)
    severity: Mapped[float] = mapped_column(Float, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ttl_s: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False, default="CANDIDATE")
    evidence_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    road_segment_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    observations: Mapped[list["HazardObservationRow"]] = relationship(back_populates="hazard", lazy="selectin")


class HazardObservationRow(Base):
    __tablename__ = "hazard_observations"

    observation_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    hazard_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("hazards.hazard_id", ondelete="SET NULL"),
        nullable=True,
    )
    source_id: Mapped[str] = mapped_column(String(128), nullable=False)
    detector_confidence: Mapped[float] = mapped_column(Float, nullable=False)
    severity_hint: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    position = mapped_column(PointGeometry(), nullable=True)
    metadata_: Mapped[dict | None] = mapped_column("metadata", JsonDocument, nullable=True, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)

    hazard: Mapped[HazardRow | None] = relationship(back_populates="observations")


class IncidentRow(Base):
    __tablename__ = "incidents"

    incident_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    incident_type: Mapped[str] = mapped_column(String(64), nullable=False)
    affected_actor_ids = mapped_column(StringArray, nullable=True)
    position = mapped_column(PointGeometry(), nullable=True)
    severity: Mapped[float] = mapped_column(Float, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    evidence = mapped_column(JsonDocument, nullable=True)


class AlertRow(Base):
    __tablename__ = "alerts"

    alert_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    alert_type: Mapped[str] = mapped_column(String(64), nullable=False)
    priority: Mapped[str] = mapped_column(String(32), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False, default="ACTIVE")
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    position = mapped_column(PointGeometry(), nullable=True)
    affected_actor_ids = mapped_column(StringArray, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    evidence = mapped_column(JsonDocument, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    policy_version: Mapped[str] = mapped_column(String(32), nullable=False, default="0.1.0")


class TrustEventRow(Base):
    __tablename__ = "trust_events"

    event_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    sender_id: Mapped[str] = mapped_column(String(128), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    detail = mapped_column(JsonDocument, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)


class SystemAuditEventRow(Base):
    __tablename__ = "system_audit_events"

    event_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    source_service: Mapped[str] = mapped_column(String(128), nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    detail = mapped_column(JsonDocument, nullable=True)
    trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
