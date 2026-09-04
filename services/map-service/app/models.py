"""SQLAlchemy 2.0 async ORM models for the map service."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Shared declarative base for all map-service models."""


class RegionModel(Base):
    """One row per imported region / bounding box."""

    __tablename__ = "regions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(256), unique=True, nullable=False, index=True)
    bbox_json: Mapped[str] = mapped_column(Text, nullable=False)
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(16), nullable=False, default="1.0")

    # Relationships
    edges: Mapped[list[RoadEdgeModel]] = relationship(
        "RoadEdgeModel", back_populates="region", cascade="all, delete-orphan"
    )
    signals: Mapped[list[TrafficSignalModel]] = relationship(
        "TrafficSignalModel", back_populates="region", cascade="all, delete-orphan"
    )
    crossings: Mapped[list[PedestrianCrossingModel]] = relationship(
        "PedestrianCrossingModel", back_populates="region", cascade="all, delete-orphan"
    )


class RoadEdgeModel(Base):
    """Stores one road edge per row."""

    __tablename__ = "road_edges"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    region_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("regions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    edge_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    from_node: Mapped[str] = mapped_column(String(128), nullable=False)
    to_node: Mapped[str] = mapped_column(String(128), nullable=False)
    length_m: Mapped[float] = mapped_column(Float, nullable=False)
    lanes: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    speed_limit_mps: Mapped[float] = mapped_column(Float, nullable=False)
    road_type: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    # JSON-encoded list of {lat, lon} dicts (stored as text for portability)
    geometry_json: Mapped[str] = mapped_column(Text, nullable=False)

    region: Mapped[RegionModel] = relationship("RegionModel", back_populates="edges")


class TrafficSignalModel(Base):
    """Stores one traffic signal per row."""

    __tablename__ = "traffic_signals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    region_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("regions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    signal_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    node_id: Mapped[str] = mapped_column(String(128), nullable=False)
    position_json: Mapped[str] = mapped_column(Text, nullable=False)
    # JSON-encoded list[str] of edge ids
    controlled_edges_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")

    region: Mapped[RegionModel] = relationship("RegionModel", back_populates="signals")


class PedestrianCrossingModel(Base):
    """Stores one pedestrian crossing per row."""

    __tablename__ = "pedestrian_crossings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    region_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("regions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    crossing_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    position_json: Mapped[str] = mapped_column(Text, nullable=False)
    edge_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

    region: Mapped[RegionModel] = relationship("RegionModel", back_populates="crossings")
