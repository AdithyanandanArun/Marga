"""Canonical, adapter-neutral contracts for adaptive signal control.

Topology comes from an OSM/SUMO adapter, while observations come from the
canonical mobility graph. Neither the controller nor its callers need to know
which simulator (if any) is currently providing the road state.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from pydantic import BaseModel, Field

from .canonical import SCHEMA_VERSION


class SignalApproachTopology(BaseModel):
    """The graph edges that form one controlled approach to a junction."""

    movement_id: str = Field(min_length=1)
    incoming_edge_ids: list[str] = Field(min_length=1)
    downstream_edge_ids: list[str] = Field(default_factory=list)
    approach_length_m: float = Field(default=100.0, gt=0)


class SignalJunctionTopology(BaseModel):
    """Explicit map matching between a traffic signal and mobility-graph edges."""

    schema_version: str = SCHEMA_VERSION
    junction_id: str = Field(min_length=1)
    signal_id: str = Field(min_length=1)
    approaches: list[SignalApproachTopology] = Field(min_length=1)
    phase_index_by_name: dict[str, int] = Field(default_factory=dict)
    phase_count: int = Field(default=4, ge=2)
    default_phase_duration_s: float = Field(default=25.0, gt=0)
    source: str = Field(description="Topology adapter or authoritative map source")


class SignalControlDecision(BaseModel):
    """Replayable, confidence-aware record of one adaptive signal decision."""

    schema_version: str = SCHEMA_VERSION
    decision_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    junction_id: str
    signal_id: str
    ts: datetime = Field(default_factory=lambda: datetime.now(UTC))
    requested_action: str
    effective_action: str
    safety_override: bool
    safety_reason: str
    applied: bool = False
    application_error: str | None = None
    confidence: float = Field(ge=0, le=1)
    policy_version: str
    evidence: list[dict[str, object]] = Field(default_factory=list)
    provenance: list[str] = Field(default_factory=list)
