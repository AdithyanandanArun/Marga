"""V2X transport and messaging schemas."""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from marga_schemas.common import ConnectivityState, GeoPoint, SchemaVersioned
from pydantic import BaseModel, Field


class MessagePriority(enum.StrEnum):
    CRITICAL_SAFETY = "CRITICAL_SAFETY"
    REGIONAL_SAFETY = "REGIONAL_SAFETY"
    OPERATIONAL = "OPERATIONAL"
    ANALYTICS = "ANALYTICS"


class QueueClass(enum.StrEnum):
    CRITICAL_LOCAL = "CRITICAL_LOCAL"
    REGIONAL_SAFETY = "REGIONAL_SAFETY"
    ANALYTICS = "ANALYTICS"


class V2XMessage(SchemaVersioned):
    message_id: UUID = Field(default_factory=uuid4)
    topic: str
    priority: MessagePriority
    sender_id: str
    sender_position: GeoPoint | None = None
    timestamp: datetime
    ttl_s: int = Field(ge=1)
    payload: dict[str, Any] = Field(default_factory=dict)
    audience_bbox: list[float] | None = None  # [minLon, minLat, maxLon, maxLat]
    audience_segment_ids: list[str] | None = None
    requires_ack: bool = False
    # Added for the direct-PC5 data plane.  Optional defaults preserve the
    # existing broker payload contract while making safety deliveries
    # explainable and replayable.
    policy_version: str | None = None
    provenance: list[str] = Field(default_factory=list)
    evidence: list[dict[str, Any]] = Field(default_factory=list)


class LinkState(SchemaVersioned):
    node_id: str
    connectivity: ConnectivityState
    direct_peers: int = 0
    cloud_reachable: bool = True
    last_cloud_contact: datetime | None = None
    last_direct_contact: datetime | None = None
    queue_depth: dict[str, int] = Field(default_factory=dict)


class StoreForwardEntry(BaseModel):
    entry_id: UUID = Field(default_factory=uuid4)
    message: V2XMessage
    queue_class: QueueClass
    enqueued_at: datetime
    retry_count: int = 0
    max_retries: int = 5
    next_retry_at: datetime | None = None
