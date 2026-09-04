"""Trust validation pipeline — the central orchestrator for message trust assessment.

Pipeline stages:
    receive -> schema validate -> timestamp/TTL -> replay cache ->
    signature/credential -> rate limit -> plausibility -> source trust ->
    domain consumer
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from marga_schemas.common import ActorType
from marga_schemas.trust import SignedMessage, TrustAssessment, TrustEvent, TrustLevel

from .credential import CredentialVerifier
from .plausibility import PlausibilityChecker
from .rate_limiter import RateLimiter
from .replay import ReplayCache

logger = logging.getLogger(__name__)


class TrustValidator:
    """Run an incoming :class:`SignedMessage` through the full trust pipeline.

    Each stage can reject the message with a specific reason.  Failed
    validations emit a :class:`TrustEvent` for audit.  The final result
    is a :class:`TrustAssessment`.
    """

    def __init__(
        self,
        *,
        replay_cache: ReplayCache | None = None,
        rate_limiter: RateLimiter | None = None,
        credential_verifier: CredentialVerifier | None = None,
        plausibility_checker: PlausibilityChecker | None = None,
    ) -> None:
        self.replay_cache = replay_cache or ReplayCache()
        self.rate_limiter = rate_limiter or RateLimiter()
        self.credential_verifier = credential_verifier or CredentialVerifier()
        self.plausibility_checker = plausibility_checker or PlausibilityChecker()

        # Audit log — collected in memory for the prototype.
        self._audit_log: list[TrustEvent] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def validate(self, message: SignedMessage) -> TrustAssessment:
        """Run the full pipeline and return a trust assessment.

        Stages are ordered so that cheap checks (expiry, replay) happen
        before expensive ones (plausibility).  Early rejection short-circuits
        the remaining stages.
        """
        try:
            from marga_observability.span import optional_span
            _span_ctx = optional_span("trust-validator", "trust.validate", {"sender": message.sender_pseudonym})
        except ImportError:
            from contextlib import nullcontext
            _span_ctx = nullcontext()

        with _span_ctx:
            return self._validate_inner(message)

    def _validate_inner(self, message: SignedMessage) -> TrustAssessment:
        sender = message.sender_pseudonym
        now = datetime.now(UTC)
        reasons: list[str] = []
        trust_level = TrustLevel.UNTRUSTED
        credential_verified = False
        plausibility_score = 0.5

        # ----- Stage 1: Schema validation (already done by Pydantic) -----
        # If we got here, the message parsed correctly.

        # ----- Stage 2: Timestamp / TTL -----
        reject_reason = self._check_timestamps(message, now)
        if reject_reason:
            reasons.append(reject_reason)
            self._emit_event(sender, "TIMESTAMP_REJECTED", {"reason": reject_reason})
            return self._build_assessment(
                sender,
                TrustLevel.UNTRUSTED,
                now,
                reasons,
                credential_verified=False,
                plausibility_score=0.0,
            )

        # ----- Stage 3: Replay cache -----
        accepted, replay_reason = self.replay_cache.check(
            sender_pseudonym=sender,
            nonce=message.nonce,
            payload_hash=message.payload_hash,
            issued_at=message.issued_at,
            expires_at=message.expires_at,
        )
        if not accepted:
            reasons.append(replay_reason)
            event_type = "TAMPERED_REPLAY" if replay_reason == "TAMPERED_REPLAY" else "REPLAY_REJECTED"
            self._emit_event(sender, event_type, {"nonce": message.nonce, "reason": replay_reason})
            return self._build_assessment(
                sender,
                TrustLevel.UNTRUSTED,
                now,
                reasons,
                credential_verified=False,
                plausibility_score=0.0,
            )

        # ----- Stage 4: Signature / Credential verification -----
        trust_level = self.credential_verifier.verify_identity(sender)
        if trust_level != TrustLevel.UNTRUSTED:
            credential_verified = True
            reasons.append(f"CREDENTIAL_OK:{trust_level.value}")
            self._emit_event(sender, "CREDENTIAL_VERIFIED", {"level": trust_level.value})
        else:
            reasons.append("CREDENTIAL_UNKNOWN")

        # Emergency credential check — fail closed.
        if message.credential_ref is not None:
            if not self.credential_verifier.verify_emergency(sender, message.credential_ref):
                reasons.append("EMERGENCY_DENIED")
                self._emit_event(
                    sender,
                    "SPOOF_DETECTED",
                    {"credential_ref": message.credential_ref},
                )
                # Down-weight but do not crash — ordinary messages continue.
                trust_level = TrustLevel.UNTRUSTED
                credential_verified = False

        # ----- Stage 5: Rate limit -----
        allowed = self.rate_limiter.check(sender, trust_level)
        rate_remaining = self.rate_limiter.get_remaining(sender)
        if not allowed:
            reasons.append("RATE_LIMITED")
            self._emit_event(sender, "RATE_LIMITED", {"remaining": rate_remaining})
            return self._build_assessment(
                sender,
                TrustLevel.UNTRUSTED,
                now,
                reasons,
                credential_verified=credential_verified,
                plausibility_score=0.0,
                rate_limit_remaining=0,
            )

        # ----- Stage 6: Plausibility -----
        plausibility_score = self._run_plausibility(message, reasons)

        # ----- Stage 7: Source trust adjustment -----
        trust_level = self._adjust_trust(trust_level, plausibility_score, credential_verified)

        return self._build_assessment(
            sender,
            trust_level,
            now,
            reasons,
            credential_verified=credential_verified,
            plausibility_score=plausibility_score,
            rate_limit_remaining=rate_remaining,
        )

    @property
    def audit_log(self) -> list[TrustEvent]:
        return list(self._audit_log)

    # ------------------------------------------------------------------
    # Pipeline stages (private)
    # ------------------------------------------------------------------

    @staticmethod
    def _check_timestamps(message: SignedMessage, now: datetime) -> str | None:
        issued = message.issued_at
        expires = message.expires_at
        if issued.tzinfo is None:
            issued = issued.replace(tzinfo=UTC)
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=UTC)

        if expires <= now:
            return "MESSAGE_EXPIRED"
        if expires <= issued:
            return "INVALID_TTL"
        # Allow up to 5 s clock skew for future timestamps.
        if issued > now + timedelta(seconds=5):
            return "FUTURE_TIMESTAMP"
        return None

    def _run_plausibility(self, message: SignedMessage, reasons: list[str]) -> float:
        """Extract motion fields from payload and run plausibility checks."""
        payload = message.payload
        if not payload:
            return 0.5  # No motion data to check.

        actor_id = payload.get("actor_id")
        lat = payload.get("lat")
        lon = payload.get("lon")
        speed_mps = payload.get("speed_mps")
        ts_raw = payload.get("ts") or payload.get("timestamp")

        if actor_id is None or lat is None or lon is None:
            return 0.5

        speed = float(speed_mps) if speed_mps is not None else 0.0

        if isinstance(ts_raw, str):
            ts = datetime.fromisoformat(ts_raw)
        elif isinstance(ts_raw, datetime):
            ts = ts_raw
        else:
            ts = message.issued_at

        actor_type_raw = payload.get("actor_type", "CAR")
        try:
            actor_type = ActorType(actor_type_raw)
        except ValueError:
            actor_type = ActorType.OTHER

        score, anomalies = self.plausibility_checker.check(
            actor_id=actor_id,
            lat=float(lat),
            lon=float(lon),
            speed_mps=speed,
            timestamp=ts,
            actor_type=actor_type,
            road_segment_id=payload.get("road_segment_id"),
        )

        if anomalies:
            reasons.extend(anomalies)
            self._emit_event(
                message.sender_pseudonym,
                "PLAUSIBILITY_ANOMALY",
                {"score": score, "anomalies": anomalies},
            )

        return score

    @staticmethod
    def _adjust_trust(
        base: TrustLevel,
        plausibility: float,
        credential_verified: bool,
    ) -> TrustLevel:
        """Down-weight trust based on plausibility score.

        A score of 0.5 is the neutral default when no motion data is
        available — it should not cause a downgrade.  Only actively
        negative scores (from detected anomalies) trigger adjustment.
        """
        if plausibility <= 0.2:
            return TrustLevel.UNTRUSTED
        if plausibility < 0.4 and base in (TrustLevel.HIGH, TrustLevel.AUTHORITY):
            return TrustLevel.MEDIUM
        return base

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _emit_event(self, sender_id: str, event_type: str, detail: dict[str, Any]) -> None:
        event = TrustEvent(
            sender_id=sender_id,
            event_type=event_type,
            timestamp=datetime.now(UTC),
            detail=detail,
        )
        self._audit_log.append(event)
        logger.info("TrustEvent: %s sender=%s %s", event_type, sender_id, detail)
        # Metrics (obj 2.5)
        if event_type in ("REPLAY_REJECTED", "TAMPERED_REPLAY", "RATE_LIMITED",
                          "SPOOF_DETECTED", "TIMESTAMP_REJECTED", "PLAUSIBILITY_ANOMALY"):
            try:
                from marga_observability.metrics import metrics as _m
                _m.trust_rejections_total.labels(reason=event_type).inc()
            except Exception:
                pass
        # NATS (obj 2.1)
        try:
            from packages.event_bus.bus import get_event_bus
            bus = get_event_bus()
            if bus and bus.connected:
                import asyncio
                try:
                    loop = asyncio.get_running_loop()
                    loop.create_task(bus.publish("trust.assessment.updated", event.model_dump(mode="json")))
                except RuntimeError:
                    pass
        except Exception:
            pass

    @staticmethod
    def _build_assessment(
        sender: str,
        trust_level: TrustLevel,
        now: datetime,
        reasons: list[str],
        *,
        credential_verified: bool,
        plausibility_score: float,
        rate_limit_remaining: int | None = None,
    ) -> TrustAssessment:
        return TrustAssessment(
            sender_id=sender,
            trust_level=trust_level,
            timestamp=now,
            reasons=reasons,
            credential_verified=credential_verified,
            plausibility_score=plausibility_score,
            rate_limit_remaining=rate_limit_remaining,
        )
