"""Unit tests for the Marga Trust and Security service."""

from __future__ import annotations

import sys
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Path setup: ensure marga_schemas and marga_trust are importable.
# ---------------------------------------------------------------------------
_repo = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_repo / "packages" / "schemas"))
sys.path.insert(0, str(_repo / "services" / "trust"))

from marga_schemas.trust import SignedMessage, TrustAssessment, TrustEvent, TrustLevel
from marga_schemas.common import ActorType

from marga_trust.replay import ReplayCache
from marga_trust.rate_limiter import RateLimiter
from marga_trust.credential import CredentialRecord, CredentialVerifier
from marga_trust.plausibility import PlausibilityChecker
from marga_trust.privacy import PseudonymManager
from marga_trust.validator import TrustValidator


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _make_message(
    *,
    sender: str = "sender-1",
    nonce: str = "nonce-001",
    payload_hash: str = "abc123",
    issued_at: datetime | None = None,
    expires_at: datetime | None = None,
    credential_ref: str | None = None,
    payload: dict | None = None,
) -> SignedMessage:
    now = _now()
    return SignedMessage(
        sender_pseudonym=sender,
        credential_ref=credential_ref,
        issued_at=issued_at or now - timedelta(seconds=1),
        expires_at=expires_at or now + timedelta(seconds=30),
        nonce=nonce,
        payload_hash=payload_hash,
        signature="sig-placeholder",
        payload=payload or {},
    )


# ===================================================================
# Replay cache tests
# ===================================================================

class TestReplayCache:

    def test_first_message_accepted(self):
        cache = ReplayCache()
        now = _now()
        ok, reason = cache.check("s1", "n1", "hash1", now, now + timedelta(seconds=30))
        assert ok is True
        assert reason == ""

    def test_duplicate_nonce_rejected(self):
        cache = ReplayCache()
        now = _now()
        cache.check("s1", "n1", "hash1", now, now + timedelta(seconds=30))
        ok, reason = cache.check("s1", "n1", "hash1", now, now + timedelta(seconds=30))
        assert ok is False
        assert reason == "DUPLICATE_NONCE"

    def test_expired_message_rejected(self):
        cache = ReplayCache()
        past = _now() - timedelta(seconds=60)
        ok, reason = cache.check("s1", "n2", "hash1", past, past + timedelta(seconds=10))
        assert ok is False
        assert reason == "MESSAGE_EXPIRED"

    def test_tampered_replay_detected(self):
        """Same sender+nonce but different payload hash must be flagged."""
        cache = ReplayCache()
        now = _now()
        cache.check("s1", "n3", "hash-original", now, now + timedelta(seconds=30))
        ok, reason = cache.check("s1", "n3", "hash-TAMPERED", now, now + timedelta(seconds=30))
        assert ok is False
        assert reason == "TAMPERED_REPLAY"

    def test_different_senders_same_nonce_ok(self):
        cache = ReplayCache()
        now = _now()
        ok1, _ = cache.check("s1", "shared-nonce", "h1", now, now + timedelta(seconds=30))
        ok2, _ = cache.check("s2", "shared-nonce", "h2", now, now + timedelta(seconds=30))
        assert ok1 is True
        assert ok2 is True


# ===================================================================
# Rate limiter tests
# ===================================================================

class TestRateLimiter:

    def test_allows_under_limit(self):
        rl = RateLimiter()
        assert rl.check("sender-a", TrustLevel.MEDIUM) is True

    def test_blocks_after_burst(self):
        """Exhaust the burst capacity and verify rejection."""
        rl = RateLimiter(
            per_sender_limits={TrustLevel.UNTRUSTED: 0.1},  # very slow refill
            per_sender_burst={TrustLevel.UNTRUSTED: 3},
        )
        assert rl.check("sender-b", TrustLevel.UNTRUSTED) is True
        assert rl.check("sender-b", TrustLevel.UNTRUSTED) is True
        assert rl.check("sender-b", TrustLevel.UNTRUSTED) is True
        # Burst exhausted.
        assert rl.check("sender-b", TrustLevel.UNTRUSTED) is False

    def test_rejection_counter(self):
        rl = RateLimiter(
            per_sender_limits={TrustLevel.UNTRUSTED: 0.01},
            per_sender_burst={TrustLevel.UNTRUSTED: 1},
        )
        rl.check("sender-c", TrustLevel.UNTRUSTED)
        rl.check("sender-c", TrustLevel.UNTRUSTED)  # rejected
        assert rl.rejection_count("sender-c") == 1
        assert rl.rejection_count() >= 1

    def test_remaining_capacity(self):
        rl = RateLimiter(
            per_sender_limits={TrustLevel.MEDIUM: 0.01},
            per_sender_burst={TrustLevel.MEDIUM: 10},
        )
        rl.check("sender-d", TrustLevel.MEDIUM)
        remaining = rl.get_remaining("sender-d")
        assert 0 <= remaining <= 10


# ===================================================================
# Credential verifier tests
# ===================================================================

class TestCredentialVerifier:

    def _verifier(self) -> CredentialVerifier:
        v = CredentialVerifier()
        v.register(CredentialRecord(
            sender_id="ambulance-1",
            trust_level=TrustLevel.AUTHORITY,
            emergency_roles=frozenset(["EMERGENCY_VEHICLE"]),
        ))
        v.register(CredentialRecord(
            sender_id="rsu-42",
            trust_level=TrustLevel.HIGH,
        ))
        v.register(CredentialRecord(
            sender_id="expired-node",
            trust_level=TrustLevel.HIGH,
            expires_at=_now() - timedelta(hours=1),
        ))
        return v

    def test_known_identity(self):
        v = self._verifier()
        assert v.verify_identity("ambulance-1") == TrustLevel.AUTHORITY

    def test_unknown_identity(self):
        v = self._verifier()
        assert v.verify_identity("random-car") == TrustLevel.UNTRUSTED

    def test_expired_credential_returns_untrusted(self):
        v = self._verifier()
        assert v.verify_identity("expired-node") == TrustLevel.UNTRUSTED

    def test_emergency_verified(self):
        v = self._verifier()
        assert v.verify_emergency("ambulance-1", "EMERGENCY_VEHICLE") is True

    def test_fake_ambulance_rejected(self):
        """A sender NOT in the allow-list cannot claim emergency privileges."""
        v = self._verifier()
        assert v.verify_emergency("random-car", "EMERGENCY_VEHICLE") is False

    def test_emergency_wrong_role_rejected(self):
        v = self._verifier()
        assert v.verify_emergency("rsu-42", "EMERGENCY_VEHICLE") is False

    def test_load_from_dict(self):
        v = CredentialVerifier()
        v.load_from_dict([
            {
                "sender_id": "node-x",
                "trust_level": "HIGH",
                "emergency_roles": ["FIRE_TRUCK"],
            }
        ])
        assert v.verify_identity("node-x") == TrustLevel.HIGH
        assert v.verify_emergency("node-x", "FIRE_TRUCK") is True


# ===================================================================
# Plausibility checker tests
# ===================================================================

class TestPlausibilityChecker:

    def test_normal_update_plausible(self):
        pc = PlausibilityChecker()
        score, anomalies = pc.check(
            actor_id="car-1", lat=12.97, lon=77.59,
            speed_mps=15.0, timestamp=_now(),
        )
        assert score == 1.0
        assert anomalies == []

    def test_teleportation_detected(self):
        pc = PlausibilityChecker(teleport_threshold_m=1000)
        t0 = _now()
        pc.check("car-2", lat=12.97, lon=77.59, speed_mps=10.0, timestamp=t0)
        # Jump ~1100 km north in 1 second.
        score, anomalies = pc.check(
            "car-2", lat=22.97, lon=77.59, speed_mps=10.0,
            timestamp=t0 + timedelta(seconds=1),
        )
        assert score == 0.0
        assert "TELEPORTATION" in anomalies

    def test_impossible_speed_flagged(self):
        pc = PlausibilityChecker()
        score, anomalies = pc.check(
            "car-3", lat=12.97, lon=77.59, speed_mps=200.0,
            timestamp=_now(), actor_type=ActorType.CAR,
        )
        assert score < 1.0
        assert "IMPOSSIBLE_SPEED" in anomalies

    def test_backwards_timestamp_rejected(self):
        pc = PlausibilityChecker()
        t0 = _now()
        pc.check("car-4", lat=12.97, lon=77.59, speed_mps=10.0, timestamp=t0)
        score, anomalies = pc.check(
            "car-4", lat=12.97, lon=77.59, speed_mps=10.0,
            timestamp=t0 - timedelta(seconds=5),
        )
        assert score == 0.0
        assert "BACKWARDS_TIMESTAMP" in anomalies

    def test_pedestrian_speed_limit_lower(self):
        pc = PlausibilityChecker()
        # 15 m/s is fine for a car but impossible for a pedestrian.
        score, anomalies = pc.check(
            "ped-1", lat=12.97, lon=77.59, speed_mps=15.0,
            timestamp=_now(), actor_type=ActorType.PEDESTRIAN,
        )
        assert score < 1.0
        assert "IMPOSSIBLE_SPEED" in anomalies


# ===================================================================
# Pseudonym manager tests
# ===================================================================

class TestPseudonymManager:

    def test_get_pseudonym_stable(self):
        pm = PseudonymManager(rotation_interval_s=300)
        p1 = pm.get_pseudonym("node-1")
        p2 = pm.get_pseudonym("node-1")
        assert p1 == p2

    def test_rotation_produces_different_value(self):
        pm = PseudonymManager()
        p1 = pm.get_pseudonym("node-2")
        p2 = pm.rotate("node-2")
        assert p1 != p2

    def test_resolve_returns_internal_id(self):
        pm = PseudonymManager()
        pseudo = pm.get_pseudonym("node-3")
        assert pm.resolve(pseudo) == "node-3"

    def test_resolve_unknown_returns_none(self):
        pm = PseudonymManager()
        assert pm.resolve("nonexistent") is None

    def test_different_nodes_get_different_pseudonyms(self):
        pm = PseudonymManager()
        p1 = pm.get_pseudonym("a")
        p2 = pm.get_pseudonym("b")
        assert p1 != p2


# ===================================================================
# Full validator pipeline tests
# ===================================================================

class TestTrustValidator:

    def _make_validator(self) -> TrustValidator:
        cred = CredentialVerifier()
        cred.register(CredentialRecord(
            sender_id="ambulance-1",
            trust_level=TrustLevel.AUTHORITY,
            emergency_roles=frozenset(["EMERGENCY_VEHICLE"]),
        ))
        cred.register(CredentialRecord(
            sender_id="rsu-42",
            trust_level=TrustLevel.HIGH,
        ))
        return TrustValidator(credential_verifier=cred)

    def test_valid_message_accepted(self):
        v = self._make_validator()
        msg = _make_message(sender="rsu-42")
        result = v.validate(msg)
        assert result.trust_level != TrustLevel.UNTRUSTED
        assert result.credential_verified is True

    def test_expired_message_rejected(self):
        v = self._make_validator()
        past = _now() - timedelta(minutes=5)
        msg = _make_message(
            expires_at=past,
            issued_at=past - timedelta(seconds=30),
        )
        result = v.validate(msg)
        assert result.trust_level == TrustLevel.UNTRUSTED
        assert "MESSAGE_EXPIRED" in result.reasons

    def test_replay_rejected(self):
        v = self._make_validator()
        msg = _make_message(nonce="replay-test")
        v.validate(msg)
        result = v.validate(msg)
        assert result.trust_level == TrustLevel.UNTRUSTED
        assert "DUPLICATE_NONCE" in result.reasons

    def test_tampered_replay_rejected_and_audited(self):
        v = self._make_validator()
        msg1 = _make_message(nonce="tamper-test", payload_hash="original")
        v.validate(msg1)
        msg2 = _make_message(nonce="tamper-test", payload_hash="MODIFIED")
        result = v.validate(msg2)
        assert result.trust_level == TrustLevel.UNTRUSTED
        assert "TAMPERED_REPLAY" in result.reasons
        # Audit log should contain the tamper event.
        tamper_events = [e for e in v.audit_log if e.event_type == "TAMPERED_REPLAY"]
        assert len(tamper_events) >= 1

    def test_fake_ambulance_emergency_denied(self):
        """Unknown sender claiming emergency credential must be rejected."""
        v = self._make_validator()
        msg = _make_message(
            sender="fake-ambulance",
            credential_ref="EMERGENCY_VEHICLE",
        )
        result = v.validate(msg)
        assert result.trust_level == TrustLevel.UNTRUSTED
        assert "EMERGENCY_DENIED" in result.reasons
        # Spoof detection audit event.
        spoof_events = [e for e in v.audit_log if e.event_type == "SPOOF_DETECTED"]
        assert len(spoof_events) >= 1

    def test_verified_emergency_accepted(self):
        v = self._make_validator()
        msg = _make_message(
            sender="ambulance-1",
            credential_ref="EMERGENCY_VEHICLE",
        )
        result = v.validate(msg)
        assert result.trust_level == TrustLevel.AUTHORITY
        assert result.credential_verified is True
        assert "EMERGENCY_DENIED" not in result.reasons

    def test_plausibility_teleport_flags_low_score(self):
        v = self._make_validator()
        t0 = _now()
        # First message at location A.
        msg1 = _make_message(
            sender="rsu-42", nonce="n-a",
            payload={
                "actor_id": "car-plaus", "lat": 12.97, "lon": 77.59,
                "speed_mps": 10.0, "ts": t0.isoformat(),
            },
        )
        v.validate(msg1)
        # Second message at location B — huge jump.
        msg2 = _make_message(
            sender="rsu-42", nonce="n-b", payload_hash="h2",
            payload={
                "actor_id": "car-plaus", "lat": 28.61, "lon": 77.20,
                "speed_mps": 10.0, "ts": (t0 + timedelta(seconds=1)).isoformat(),
            },
        )
        result = v.validate(msg2)
        assert result.plausibility_score < 0.5

    def test_rate_limited_sender_blocked(self):
        rl = RateLimiter(
            per_sender_limits={TrustLevel.UNTRUSTED: 0.001},
            per_sender_burst={TrustLevel.UNTRUSTED: 1},
        )
        v = TrustValidator(rate_limiter=rl)
        msg1 = _make_message(sender="spammer", nonce="s1")
        r1 = v.validate(msg1)
        msg2 = _make_message(sender="spammer", nonce="s2", payload_hash="h2")
        r2 = v.validate(msg2)
        assert r2.trust_level == TrustLevel.UNTRUSTED
        assert "RATE_LIMITED" in r2.reasons

    def test_backwards_timestamp_in_payload(self):
        v = self._make_validator()
        t0 = _now()
        msg1 = _make_message(
            sender="rsu-42", nonce="bt-1",
            payload={
                "actor_id": "car-bt", "lat": 12.97, "lon": 77.59,
                "speed_mps": 5.0, "ts": t0.isoformat(),
            },
        )
        v.validate(msg1)
        msg2 = _make_message(
            sender="rsu-42", nonce="bt-2", payload_hash="bth2",
            payload={
                "actor_id": "car-bt", "lat": 12.97, "lon": 77.59,
                "speed_mps": 5.0, "ts": (t0 - timedelta(seconds=10)).isoformat(),
            },
        )
        result = v.validate(msg2)
        assert "BACKWARDS_TIMESTAMP" in result.reasons
        assert result.plausibility_score == 0.0
