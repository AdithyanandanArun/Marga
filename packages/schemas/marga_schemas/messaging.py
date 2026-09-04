"""V2X transport and messaging schemas."""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from marga_schemas.common import ConnectivityState, GeoPoint, SchemaVersioned


class MessagePriority(str, enum.Enum):
    CRITICAL_SAFETY = "CRITICAL_SAFETY"
    REGIONAL_SAFETY = "REGIONAL_SAFETY"
    OPERATIONAL = "OPERATIONAL"
    ANALYTICS = "ANALYTICS"


class QueueClass(str, enum.Enum):
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
