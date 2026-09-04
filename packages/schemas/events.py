"""Broker-neutral envelope for all canonical internal events."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Generic, TypeVar
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, field_validator

from .contracts import CONTRACT_VERSION, MODEL_CONFIG
from .models import SchemaVersion, _utc

PayloadT = TypeVar("PayloadT")


class EventType(StrEnum):
    ACTOR_STATE_UPDATED = "actor.state.updated"
    INFRASTRUCTURE_SIGNAL_UPDATED = "infrastructure.signal.updated"
    HAZARD_OBSERVED = "hazard.observed"
    HAZARD_UPDATED = "hazard.updated"
    POSITION_ESTIMATE_UPDATED = "position.estimate.updated"
    TRUST_ASSESSMENT_UPDATED = "trust.assessment.updated"
    RISK_DETECTED = "risk.detected"
    ALERT_ISSUED = "alert.issued"
    ALERT_CLEARED = "alert.cleared"
    CONNECTIVITY_CHANGED = "connectivity.changed"
    ROAD_EVENT_UPDATED = "road.event.updated"
    SYSTEM_FAILURE_INJECTED = "system.failure.injected"


class EventEnvelope(BaseModel, Generic[PayloadT]):
    """Versioned event wrapper; consumers must deduplicate by ``event_id``."""

    model_config = MODEL_CONFIG
    event_id: UUID = Field(default_factory=uuid4)
    event_type: EventType | str
    schema_version: SchemaVersion = CONTRACT_VERSION
    produced_at: datetime
    source_service: str = Field(min_length=1, max_length=128)
    payload: PayloadT
    correlation_id: UUID | None = None
    actor_id: str | None = None
    trace_id: str | None = None

    _produced_at_utc = field_validator("produced_at")(_utc)
