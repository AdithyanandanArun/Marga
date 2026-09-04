"""Alert schema for safety warnings issued by the Marga risk engine."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator

from .common import Position


class AlertSeverity(str, Enum):
    """Severity classification for alerts."""

    info = "info"
    warning = "warning"
    critical = "critical"
    emergency = "emergency"


class Alert(BaseModel):
    """A safety alert issued by the Marga risk engine to one or more actors."""

    schema_version: str = Field("1.0", description="Schema version string")
    alert_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Unique alert identifier (UUID)",
    )
    timestamp_utc: datetime = Field(..., description="UTC timestamp when alert was issued")
    severity: AlertSeverity = Field(..., description="Alert severity level")
    alert_type: str = Field(..., description="Alert category identifier")
    affected_actor_ids: list[str] = Field(
        ..., description="IDs of actors this alert pertains to"
    )
    position: Optional[Position] = Field(
        None, description="Geographic location associated with this alert"
    )
    confidence: float = Field(..., description="Alert confidence score in [0, 1]")
    evidence: dict[str, Any] = Field(
        default_factory=dict, description="Supporting evidence for this alert"
    )
    policy_version: str = Field(..., description="Risk policy version that generated this alert")
    expires_at: Optional[datetime] = Field(
        None, description="UTC time after which the alert should be considered stale"
    )
    trace_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Distributed trace identifier (UUID)",
    )

    @field_validator("confidence")
    @classmethod
    def confidence_in_range(cls, v: float) -> float:
        if not (0.0 <= v <= 1.0):
            raise ValueError(f"confidence must be in [0, 1], got {v}")
        return v
