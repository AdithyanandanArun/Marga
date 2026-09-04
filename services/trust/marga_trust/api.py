"""FastAPI router for the Trust and Security service."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from marga_schemas.trust import SignedMessage, TrustAssessment, TrustLevel

from marga_trust.credential import CredentialVerifier
from marga_trust.rate_limiter import RateLimiter
from marga_trust.replay import ReplayCache
from marga_trust.plausibility import PlausibilityChecker
from marga_trust.validator import TrustValidator

# ---------------------------------------------------------------
# Singleton components (shared across requests in this process)
# ---------------------------------------------------------------
_replay_cache = ReplayCache()
_rate_limiter = RateLimiter()
_credential_verifier = CredentialVerifier()
_plausibility_checker = PlausibilityChecker()

_validator = TrustValidator(
    replay_cache=_replay_cache,
    rate_limiter=_rate_limiter,
    credential_verifier=_credential_verifier,
    plausibility_checker=_plausibility_checker,
)

router = APIRouter(prefix="/v1/trust", tags=["trust"])


# ---------------------------------------------------------------
# Request / response models for endpoints not covered by schemas
# ---------------------------------------------------------------
class EmergencyVerifyRequest(BaseModel):
    sender_id: str
    credential_ref: str


class EmergencyVerifyResponse(BaseModel):
    verified: bool
    sender_id: str
    credential_ref: str


class RateLimitStatus(BaseModel):
    sender_id: str
    remaining: int
    rejections: int


class HealthResponse(BaseModel):
    status: str
    timestamp: str
    replay_cache_size: int


# ---------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------

@router.post("/validate", response_model=TrustAssessment)
async def validate_message(message: SignedMessage) -> TrustAssessment:
    """Validate a signed message through the full trust pipeline."""
    return _validator.validate(message)


@router.get("/identity/{sender_id}")
async def get_identity(sender_id: str) -> dict:
    """Return the trust level for a known identity."""
    level = _credential_verifier.verify_identity(sender_id)
    return {
        "sender_id": sender_id,
        "trust_level": level.value,
        "known": level != TrustLevel.UNTRUSTED,
    }


@router.post("/verify-emergency", response_model=EmergencyVerifyResponse)
async def verify_emergency(request: EmergencyVerifyRequest) -> EmergencyVerifyResponse:
    """Verify emergency credentials for a sender."""
    verified = _credential_verifier.verify_emergency(
        request.sender_id, request.credential_ref
    )
    return EmergencyVerifyResponse(
        verified=verified,
        sender_id=request.sender_id,
        credential_ref=request.credential_ref,
    )


@router.get("/rate-limit/{sender_id}", response_model=RateLimitStatus)
async def rate_limit_status(sender_id: str) -> RateLimitStatus:
    """Check rate limit status for a sender."""
    return RateLimitStatus(
        sender_id=sender_id,
        remaining=_rate_limiter.get_remaining(sender_id),
        rejections=_rate_limiter.rejection_count(sender_id),
    )


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Health check endpoint."""
    return HealthResponse(
        status="ok",
        timestamp=datetime.now(timezone.utc).isoformat(),
        replay_cache_size=_replay_cache.size,
    )


# ---------------------------------------------------------------
# Component accessors (for tests / programmatic config)
# ---------------------------------------------------------------

def get_validator() -> TrustValidator:
    return _validator


def get_credential_verifier() -> CredentialVerifier:
    return _credential_verifier
