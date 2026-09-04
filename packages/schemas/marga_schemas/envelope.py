"""Canonical event envelope — every internal message uses this wrapper."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Generic, TypeVar
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

T = TypeVar("T")


class EventEnvelope(BaseModel, Generic[T]):
    event_id: UUID = Field(default_factory=uuid4)
    event_type: str
    schema_version: str = "0.1.0"
    produced_at: datetime = Field(default_factory=datetime.utcnow)
    source_service: str
    correlation_id: UUID | None = None
    actor_id: str | None = None
    trace_id: str | None = None
    payload: Any = None  # typed T at runtime via model_validate
