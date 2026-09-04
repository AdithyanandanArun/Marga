"""Replay attack defense — in-memory nonce/event_id deduplication with TTL cleanup."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _CacheEntry:
    """An entry in the replay cache, tracking the payload hash and expiry."""

    payload_hash: str
    expires_at: float  # monotonic clock deadline for cleanup
    received_at: float = field(default_factory=time.monotonic)


class ReplayCache:
    """Detects replayed or expired signed messages.

    Keyed by ``(sender_pseudonym, nonce)``.  A message is rejected when:
    * its ``issued_at + TTL`` is in the past (expired),
    * its nonce has already been seen (replay), or
    * it re-uses a nonce but carries a different payload hash (tampered replay).

    Periodic cleanup removes entries whose TTL has elapsed so the cache
    does not grow without bound.
    """

    def __init__(
        self,
        *,
        cleanup_interval_s: float = 60.0,
        extra_retention_s: float = 30.0,
    ) -> None:
        # (sender_pseudonym, nonce) -> _CacheEntry
        self._cache: dict[tuple[str, str], _CacheEntry] = {}
        self._lock = threading.Lock()
        self._cleanup_interval_s = cleanup_interval_s
        self._extra_retention_s = extra_retention_s
        self._last_cleanup = time.monotonic()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def check(
        self,
        sender_pseudonym: str,
        nonce: str,
        payload_hash: str,
        issued_at: datetime,
        expires_at: datetime,
    ) -> tuple[bool, str]:
        """Check whether a message should be accepted.

        Returns ``(accepted, reason)`` — *reason* is empty on acceptance.
        """
        now_utc = datetime.now(timezone.utc)

        # 1. Expired message?
        if expires_at <= now_utc:
            logger.info("Replay-cache: expired message from %s nonce=%s", sender_pseudonym, nonce)
            return False, "MESSAGE_EXPIRED"

        # 2. Issued in the future (clock skew tolerance: 5 s)?
        if issued_at > now_utc.replace(microsecond=0) + timedelta(seconds=5):
            logger.warning("Replay-cache: future timestamp from %s", sender_pseudonym)
            return False, "FUTURE_TIMESTAMP"

        key = (sender_pseudonym, nonce)

        with self._lock:
            self._maybe_cleanup()

            existing = self._cache.get(key)
            if existing is not None:
                if existing.payload_hash != payload_hash:
                    logger.warning(
                        "Replay-cache: tampered replay from %s nonce=%s",
                        sender_pseudonym,
                        nonce,
                    )
                    return False, "TAMPERED_REPLAY"
                logger.info("Replay-cache: duplicate nonce from %s nonce=%s", sender_pseudonym, nonce)
                return False, "DUPLICATE_NONCE"

            # Compute monotonic expiry for cache cleanup.
            remaining_ttl = (expires_at - now_utc).total_seconds()
            mono_expiry = time.monotonic() + remaining_ttl + self._extra_retention_s

            self._cache[key] = _CacheEntry(
                payload_hash=payload_hash,
                expires_at=mono_expiry,
            )
        return True, ""

    @property
    def size(self) -> int:
        with self._lock:
            return len(self._cache)

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _maybe_cleanup(self) -> None:
        """Remove entries whose retention window has elapsed (called under lock)."""
        now = time.monotonic()
        if now - self._last_cleanup < self._cleanup_interval_s:
            return
        before = len(self._cache)
        self._cache = {k: v for k, v in self._cache.items() if v.expires_at > now}
        after = len(self._cache)
        self._last_cleanup = now
        if before != after:
            logger.debug("Replay-cache cleanup: %d -> %d entries", before, after)
