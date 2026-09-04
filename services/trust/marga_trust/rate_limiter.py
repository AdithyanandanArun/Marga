"""Per-sender and global rate limiting using token-bucket algorithm."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field

from marga_schemas.trust import TrustLevel

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------
# Default limits (messages per second) by trust level
# ---------------------------------------------------------------
DEFAULT_LIMITS: dict[TrustLevel, float] = {
    TrustLevel.UNTRUSTED: 2.0,
    TrustLevel.LOW: 5.0,
    TrustLevel.MEDIUM: 20.0,
    TrustLevel.HIGH: 50.0,
    TrustLevel.AUTHORITY: 200.0,
}

DEFAULT_BURST: dict[TrustLevel, int] = {
    TrustLevel.UNTRUSTED: 4,
    TrustLevel.LOW: 10,
    TrustLevel.MEDIUM: 40,
    TrustLevel.HIGH: 100,
    TrustLevel.AUTHORITY: 400,
}

DEFAULT_GLOBAL_RATE: float = 500.0
DEFAULT_GLOBAL_BURST: int = 1000


@dataclass
class _Bucket:
    """Token bucket state for a single sender."""

    tokens: float
    capacity: int
    refill_rate: float  # tokens/sec
    last_refill: float = field(default_factory=time.monotonic)

    def refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self.last_refill
        self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_rate)
        self.last_refill = now

    def try_consume(self, n: int = 1) -> bool:
        self.refill()
        if self.tokens >= n:
            self.tokens -= n
            return True
        return False

    @property
    def remaining(self) -> int:
        self.refill()
        return int(self.tokens)


class RateLimiter:
    """Token-bucket rate limiter with per-sender and global limits.

    * Each sender gets a bucket sized according to its :class:`TrustLevel`.
    * A global bucket protects overall system capacity.
    * Callers that exceed their limit are rejected and a counter is
      incremented for metrics export.
    """

    def __init__(
        self,
        *,
        per_sender_limits: dict[TrustLevel, float] | None = None,
        per_sender_burst: dict[TrustLevel, int] | None = None,
        global_rate: float = DEFAULT_GLOBAL_RATE,
        global_burst: int = DEFAULT_GLOBAL_BURST,
    ) -> None:
        self._limits = per_sender_limits or dict(DEFAULT_LIMITS)
        self._burst = per_sender_burst or dict(DEFAULT_BURST)
        self._buckets: dict[str, _Bucket] = {}
        self._global = _Bucket(
            tokens=float(global_burst),
            capacity=global_burst,
            refill_rate=global_rate,
        )
        self._lock = threading.Lock()
        # Simple counters for metrics (sender_id -> rejection count).
        self._rejections: dict[str, int] = {}
        self._total_rejections: int = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def check(self, sender_id: str, trust_level: TrustLevel = TrustLevel.UNTRUSTED) -> bool:
        """Return ``True`` if the message from *sender_id* is allowed."""
        with self._lock:
            # Global gate first.
            if not self._global.try_consume():
                self._record_rejection(sender_id)
                logger.warning("Rate-limiter: global limit reached, rejecting %s", sender_id)
                return False

            bucket = self._get_or_create(sender_id, trust_level)
            if not bucket.try_consume():
                self._record_rejection(sender_id)
                logger.info("Rate-limiter: per-sender limit for %s", sender_id)
                return False
        return True

    def get_remaining(self, sender_id: str) -> int:
        """Return the approximate number of tokens remaining for *sender_id*."""
        with self._lock:
            bucket = self._buckets.get(sender_id)
            if bucket is None:
                return 0
            return bucket.remaining

    def rejection_count(self, sender_id: str | None = None) -> int:
        """Return rejection count — total if *sender_id* is ``None``."""
        with self._lock:
            if sender_id is None:
                return self._total_rejections
            return self._rejections.get(sender_id, 0)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _get_or_create(self, sender_id: str, trust_level: TrustLevel) -> _Bucket:
        bucket = self._buckets.get(sender_id)
        if bucket is not None:
            return bucket
        rate = self._limits.get(trust_level, DEFAULT_LIMITS[TrustLevel.UNTRUSTED])
        burst = self._burst.get(trust_level, DEFAULT_BURST[TrustLevel.UNTRUSTED])
        bucket = _Bucket(tokens=float(burst), capacity=burst, refill_rate=rate)
        self._buckets[sender_id] = bucket
        return bucket

    def _record_rejection(self, sender_id: str) -> None:
        self._rejections[sender_id] = self._rejections.get(sender_id, 0) + 1
        self._total_rejections += 1
