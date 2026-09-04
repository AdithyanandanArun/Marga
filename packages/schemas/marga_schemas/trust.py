"""Trust, authentication, and signed message schemas."""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from marga_schemas.common import SchemaVersioned
from pydantic import Field


class TrustLevel(str, enum.Enum):
    UNTRUSTED = "UNTRUSTED"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    AUTHORITY = "AUTHORITY"


class TrustAssessment(SchemaVersioned):
    assessment_id: UUID = Field(default_factory=uuid4)
    sender_id: str
    trust_level: TrustLevel
    timestamp: datetime
    reasons: list[str] = Field(default_factory=list)
    rate_limit_remaining: int | None = None
    credential_verified: bool = False
    plausibility_score: float = Field(ge=0, le=1, default=0.5)


class SignedMessage(SchemaVersioned):
    sender_pseudonym: str
    credential_ref: str | None = None
    issued_at: datetime
    expires_at: datetime
    nonce: str
    payload_hash: str
    signature: str
    payload: dict[str, Any] = Field(default_factory=dict)


class TrustEvent(SchemaVersioned):
    event_id: UUID = Field(default_factory=uuid4)
    sender_id: str
    event_type: str  # REPLAY_REJECTED, RATE_LIMITED, SPOOF_DETECTED, CREDENTIAL_VERIFIED, etc.
    timestamp: datetime
    detail: dict[str, Any] = Field(default_factory=dict)
