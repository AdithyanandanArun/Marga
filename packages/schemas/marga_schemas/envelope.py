"""Canonical event envelope — every internal message uses this wrapper."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class EventEnvelope[T](BaseModel):
    event_id: UUID = Field(default_factory=uuid4)
    event_type: str
    schema_version: str = "0.1.0"
    produced_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    source_service: str
    correlation_id: UUID | None = None
    actor_id: str | None = None
    trace_id: str | None = None
    payload: Any = None  # typed T at runtime via model_validate
