"""Alert prioritization and lifecycle schemas."""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from marga_schemas.common import EvidenceItem, GeoPoint, SchemaVersioned


class AlertPriority(str, enum.Enum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    INFO = "INFO"


class AlertState(str, enum.Enum):
    ACTIVE = "ACTIVE"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    RESOLVED = "RESOLVED"
    EXPIRED = "EXPIRED"
    SUPPRESSED = "SUPPRESSED"


class Alert(SchemaVersioned):
    alert_id: UUID = Field(default_factory=uuid4)
    alert_type: str
    priority: AlertPriority
    state: AlertState = AlertState.ACTIVE
    title: str
    description: str
    position: GeoPoint | None = None
    affected_actor_ids: list[str] = Field(default_factory=list)
    risk_id: UUID | None = None
    hazard_id: UUID | None = None
    confidence: float = Field(ge=0, le=1)
    evidence: list[EvidenceItem] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    expires_at: datetime | None = None
    ttl_s: int | None = None
    policy_version: str = "0.1.0"
    machine_reasoning: dict[str, Any] = Field(default_factory=dict)
    driver_text: str | None = None
