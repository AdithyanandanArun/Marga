"""Initial schema — hazards, observations, incidents, alerts, trust events, audit events.

Revision ID: 0001
Revises: None
Create Date: 2026-09-04
"""
from typing import Sequence, Union

import geoalchemy2
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Enable PostGIS extension
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")

    op.create_table(
        "hazards",
        sa.Column("hazard_id", sa.Uuid(), primary_key=True),
        sa.Column("hazard_type", sa.String(64), nullable=False),
        sa.Column(
            "position",
            geoalchemy2.Geometry(geometry_type="POINT", srid=4326),
            nullable=False,
        ),
        sa.Column("severity", sa.Float(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("first_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ttl_s", sa.Integer(), nullable=False),
        sa.Column(
            "state", sa.String(32), nullable=False, server_default="CANDIDATE"
        ),
        sa.Column("evidence_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("road_segment_id", sa.String(128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    op.create_table(
        "hazard_observations",
        sa.Column("observation_id", sa.Uuid(), primary_key=True),
        sa.Column("hazard_id", sa.Uuid(), nullable=True),
        sa.Column("source_id", sa.String(128), nullable=False),
        sa.Column("detector_confidence", sa.Float(), nullable=False),
        sa.Column(
            "severity_hint", sa.Float(), nullable=False, server_default="0.5"
        ),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "position",
            geoalchemy2.Geometry(geometry_type="POINT", srid=4326),
            nullable=True,
        ),
        sa.Column("metadata", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["hazard_id"], ["hazards.hazard_id"], ondelete="SET NULL"
        ),
    )

    op.create_table(
        "incidents",
        sa.Column("incident_id", sa.Uuid(), primary_key=True),
        sa.Column("incident_type", sa.String(64), nullable=False),
        sa.Column("affected_actor_ids", sa.ARRAY(sa.String()), nullable=True),
        sa.Column(
            "position",
            geoalchemy2.Geometry(geometry_type="POINT", srid=4326),
            nullable=True,
        ),
        sa.Column("severity", sa.Float(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("evidence", postgresql.JSONB(), nullable=True),
    )

    op.create_table(
        "alerts",
        sa.Column("alert_id", sa.Uuid(), primary_key=True),
        sa.Column("alert_type", sa.String(64), nullable=False),
        sa.Column("priority", sa.String(32), nullable=False),
        sa.Column(
            "state", sa.String(32), nullable=False, server_default="ACTIVE"
        ),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column(
            "position",
            geoalchemy2.Geometry(geometry_type="POINT", srid=4326),
            nullable=True,
        ),
        sa.Column("affected_actor_ids", sa.ARRAY(sa.String()), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("evidence", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "policy_version",
            sa.String(32),
            nullable=False,
            server_default="0.1.0",
        ),
    )

    op.create_table(
        "trust_events",
        sa.Column("event_id", sa.Uuid(), primary_key=True),
        sa.Column("sender_id", sa.String(128), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("detail", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    op.create_table(
        "system_audit_events",
        sa.Column("event_id", sa.Uuid(), primary_key=True),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("source_service", sa.String(128), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("detail", postgresql.JSONB(), nullable=True),
        sa.Column("trace_id", sa.String(64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    # Indices for common query patterns
    op.create_index("ix_hazards_state", "hazards", ["state"])
    op.create_index("ix_hazards_road_segment", "hazards", ["road_segment_id"])
    op.create_index(
        "ix_hazard_observations_hazard_id",
        "hazard_observations",
        ["hazard_id"],
    )
    op.create_index("ix_alerts_state", "alerts", ["state"])
    op.create_index("ix_alerts_priority", "alerts", ["priority"])
    op.create_index(
        "ix_trust_events_sender_id", "trust_events", ["sender_id"]
    )
    op.create_index(
        "ix_system_audit_events_type",
        "system_audit_events",
        ["event_type"],
    )
    op.create_index(
        "ix_system_audit_events_trace_id",
        "system_audit_events",
        ["trace_id"],
    )

    # Spatial indices (PostGIS GIST)
    op.create_index(
        "ix_hazards_position",
        "hazards",
        ["position"],
        postgresql_using="gist",
    )
    op.create_index(
        "ix_alerts_position",
        "alerts",
        ["position"],
        postgresql_using="gist",
    )


def downgrade() -> None:
    op.drop_table("system_audit_events")
    op.drop_table("trust_events")
    op.drop_table("alerts")
    op.drop_table("incidents")
    op.drop_table("hazard_observations")
    op.drop_table("hazards")
