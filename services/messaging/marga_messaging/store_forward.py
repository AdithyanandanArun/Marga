"""Store-and-forward / delay-tolerant messaging for V2X."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from uuid import UUID

from marga_schemas.common import ConnectivityState
from marga_schemas.messaging import QueueClass, StoreForwardEntry, V2XMessage

logger = logging.getLogger(__name__)

# Mapping from QueueClass to the connectivity states in which messages
# of that class may be forwarded.
_FORWARD_ELIGIBILITY: dict[QueueClass, set[ConnectivityState]] = {
    # Critical local messages are delivered immediately regardless of connectivity.
    QueueClass.CRITICAL_LOCAL: {
        ConnectivityState.FULL,
        ConnectivityState.DIRECT_ONLY,
        ConnectivityState.INTERMITTENT,
        ConnectivityState.ISOLATED,
    },
    # Regional safety messages require at least some connectivity.
    QueueClass.REGIONAL_SAFETY: {
        ConnectivityState.FULL,
        ConnectivityState.DIRECT_ONLY,
        ConnectivityState.INTERMITTENT,
    },
    # Analytics can wait for full connectivity.
    QueueClass.ANALYTICS: {
        ConnectivityState.FULL,
    },
}


class StoreForwardManager:
    """Delay-tolerant store-and-forward manager.

    Responsibilities:
      - Queue non-urgent events when cloud connectivity is absent
      - Critical collision messages are NEVER delayed behind cloud sync
      - Deduplication using message_id + schema_version (event_id/hazard_id analog)
      - Expire entries whose TTL has passed
      - Forward only when connectivity permits the queue class
    """

    def __init__(self) -> None:
        self._entries: dict[UUID, StoreForwardEntry] = {}
        # Dedup index: (message_id, schema_version) -> entry_id
        self._seen: dict[tuple[UUID, str], UUID] = {}
        self._total_stored = 0
        self._total_forwarded = 0
        self._total_expired = 0

    def store(self, message: V2XMessage, queue_class: QueueClass) -> StoreForwardEntry:
        """Store a message for later forwarding.

        Deduplication: if a message with the same message_id and schema_version
        already exists, the entry is updated with the newer message if the
        incoming timestamp is more recent (latest-version semantics).

        Returns the StoreForwardEntry (new or updated).
        """
        dedup_key = (message.message_id, message.schema_version)
        now = datetime.now(UTC)

        # Check for duplicate.
        existing_entry_id = self._seen.get(dedup_key)
        if existing_entry_id is not None and existing_entry_id in self._entries:
            existing = self._entries[existing_entry_id]
            incoming_ts = (
                message.timestamp if message.timestamp.tzinfo else message.timestamp.replace(tzinfo=UTC)
            )
            existing_ts = existing.message.timestamp
            if existing_ts.tzinfo is None:
                existing_ts = existing_ts.replace(tzinfo=UTC)

            if incoming_ts > existing_ts:
                # Update with newer version.
                existing.message = message
                existing.queue_class = queue_class
                existing.retry_count = 0
                logger.debug(
                    "Updated store-forward entry %s with newer message version",
                    existing.entry_id,
                )
            return existing

        entry = StoreForwardEntry(
            message=message,
            queue_class=queue_class,
            enqueued_at=now,
        )
        self._entries[entry.entry_id] = entry
        self._seen[dedup_key] = entry.entry_id
        self._total_stored += 1
        return entry

    def flush(self, connectivity: ConnectivityState) -> list[V2XMessage]:
        """Return messages eligible for delivery given current connectivity.

        Eligible messages are removed from the store. CRITICAL_LOCAL messages
        are always eligible (delivered immediately, never queued behind cloud sync).
        """
        now = datetime.now(UTC)
        eligible: list[V2XMessage] = []
        to_remove: list[UUID] = []

        # Collect eligible entries, prioritizing CRITICAL_LOCAL first.
        sorted_entries = sorted(
            self._entries.values(),
            key=lambda e: (
                0
                if e.queue_class == QueueClass.CRITICAL_LOCAL
                else 1
                if e.queue_class == QueueClass.REGIONAL_SAFETY
                else 2
            ),
        )

        for entry in sorted_entries:
            # Check TTL expiration.
            msg = entry.message
            ts = msg.timestamp if msg.timestamp.tzinfo else msg.timestamp.replace(tzinfo=UTC)
            if (now - ts).total_seconds() > msg.ttl_s:
                to_remove.append(entry.entry_id)
                self._total_expired += 1
                continue

            # Check forward eligibility by connectivity state.
            allowed_states = _FORWARD_ELIGIBILITY.get(entry.queue_class, set())
            if connectivity in allowed_states:
                eligible.append(msg)
                to_remove.append(entry.entry_id)
                self._total_forwarded += 1

        # Remove forwarded and expired entries.
        for entry_id in to_remove:
            entry = self._entries.pop(entry_id, None)
            if entry is not None:
                dedup_key = (entry.message.message_id, entry.message.schema_version)
                self._seen.pop(dedup_key, None)

        return eligible

    def cleanup(self) -> int:
        """Remove entries whose TTL has passed. Returns count removed."""
        now = datetime.now(UTC)
        expired: list[UUID] = []

        for entry_id, entry in self._entries.items():
            msg = entry.message
            ts = msg.timestamp if msg.timestamp.tzinfo else msg.timestamp.replace(tzinfo=UTC)
            if (now - ts).total_seconds() > msg.ttl_s:
                expired.append(entry_id)

        for entry_id in expired:
            entry = self._entries.pop(entry_id, None)
            if entry is not None:
                dedup_key = (entry.message.message_id, entry.message.schema_version)
                self._seen.pop(dedup_key, None)

        self._total_expired += len(expired)
        return len(expired)

    def get_stats(self) -> dict[str, int]:
        """Return current queue stats."""
        per_class: dict[str, int] = {}
        for entry in self._entries.values():
            key = entry.queue_class.value
            per_class[key] = per_class.get(key, 0) + 1

        return {
            "pending": len(self._entries),
            "total_stored": self._total_stored,
            "total_forwarded": self._total_forwarded,
            "total_expired": self._total_expired,
            **per_class,
        }
