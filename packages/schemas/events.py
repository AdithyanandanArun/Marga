"""Event envelope for internal message bus.

All events on the bus are wrapped in this envelope with correlation,
tracing, schema version, and source metadata.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


class EventEnvelope(BaseModel):
    """Standard event wrapper for all internal messages."""

    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    event_type: str  # e.g. "actor.state.updated", "risk.detected", "alert.issued"
    schema_version: str = "0.1.0"
    ts: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    source_service: str
    correlation_id: str | None = None
    trace_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    payload: dict[str, Any]
