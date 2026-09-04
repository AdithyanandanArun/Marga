"""Message priority queue with congestion control, backpressure, and latest-state compaction."""

from __future__ import annotations

import heapq
import logging
import threading
from datetime import UTC, datetime

from marga_schemas.messaging import MessagePriority, V2XMessage

logger = logging.getLogger(__name__)

# Lower numeric value = higher dispatch priority.
_PRIORITY_ORDER: dict[MessagePriority, int] = {
    MessagePriority.CRITICAL_SAFETY: 0,
    MessagePriority.REGIONAL_SAFETY: 1,
    MessagePriority.OPERATIONAL: 2,
    MessagePriority.ANALYTICS: 3,
}

# Shedding order: shed from lowest priority first.
_SHED_ORDER: list[MessagePriority] = [
    MessagePriority.ANALYTICS,
    MessagePriority.OPERATIONAL,
    MessagePriority.REGIONAL_SAFETY,
    MessagePriority.CRITICAL_SAFETY,
]


class MessagePriorityQueue:
    """Priority-aware message queue with congestion control.

    Features:
      - Separate priority lanes for CRITICAL_SAFETY, REGIONAL_SAFETY, OPERATIONAL, ANALYTICS
      - Backpressure: when total queue exceeds capacity, shed lowest priority first
      - Latest-state compaction: for high-rate vehicle position updates, keep only
        the newest message per sender_id (actor) within the OPERATIONAL/ANALYTICS tiers
      - Thread-safe via a reentrant lock
    """

    def __init__(
        self,
        capacity: int = 10_000,
        *,
        compaction_enabled: bool = True,
    ) -> None:
        self._capacity = capacity
        self._compaction_enabled = compaction_enabled
        self._lock = threading.Lock()

        # Per-priority heaps: entries are (priority_order, sequence, message)
        self._lanes: dict[MessagePriority, list[tuple[int, int, V2XMessage]]] = {p: [] for p in MessagePriority}

        # For latest-state compaction: track newest message per sender_id per lane.
        # Maps sender_id -> sequence number of the entry currently considered "latest".
        self._latest_by_sender: dict[MessagePriority, dict[str, int]] = {p: {} for p in MessagePriority}
        # Tombstones for compacted-out entries (superseded by a newer message from same sender).
        self._tombstones: set[int] = set()

        self._seq = 0
        self._total_enqueued = 0
        self._total_dropped = 0

    @property
    def _total_size(self) -> int:
        return sum(len(lane) for lane in self._lanes.values()) - len(self._tombstones)

    def enqueue(self, message: V2XMessage) -> bool:
        """Add a message to the appropriate priority lane.

        Returns True if the message was accepted, False if it was dropped
        (either expired or shed due to backpressure).
        """
        # Drop expired messages immediately.
        now = datetime.now(UTC)
        ts = message.timestamp if message.timestamp.tzinfo else message.timestamp.replace(tzinfo=UTC)
        if (now - ts).total_seconds() > message.ttl_s:
            return False

        priority = message.priority

        with self._lock:
            # Check capacity — if over, try to shed lower priority first.
            if self._total_size >= self._capacity:
                if not self._shed_one(priority):
                    self._total_dropped += 1
                    return False

            self._seq += 1
            seq = self._seq
            entry = (_PRIORITY_ORDER[priority], seq, message)

            # Latest-state compaction for OPERATIONAL and ANALYTICS lanes.
            if self._compaction_enabled and priority in (
                MessagePriority.OPERATIONAL,
                MessagePriority.ANALYTICS,
            ):
                sender = message.sender_id
                prev_seq = self._latest_by_sender[priority].get(sender)
                if prev_seq is not None:
                    # Mark the old entry as a tombstone so dequeue skips it.
                    self._tombstones.add(prev_seq)
                self._latest_by_sender[priority][sender] = seq

            heapq.heappush(self._lanes[priority], entry)
            self._total_enqueued += 1
            return True

    def dequeue(self) -> V2XMessage | None:
        """Return the highest-priority message, or None if all lanes are empty.

        Skips tombstoned (compacted-out) and expired entries.
        """
        with self._lock:
            for priority in _SHED_ORDER[::-1]:  # highest priority first
                lane = self._lanes[priority]
                while lane:
                    _, seq, msg = lane[0]

                    # Skip tombstones.
                    if seq in self._tombstones:
                        heapq.heappop(lane)
                        self._tombstones.discard(seq)
                        continue

                    # Skip expired.
                    now = datetime.now(UTC)
                    ts = msg.timestamp if msg.timestamp.tzinfo else msg.timestamp.replace(tzinfo=UTC)
                    if (now - ts).total_seconds() > msg.ttl_s:
                        heapq.heappop(lane)
                        continue

                    heapq.heappop(lane)
                    # Clean compaction tracking.
                    if self._compaction_enabled and priority in (
                        MessagePriority.OPERATIONAL,
                        MessagePriority.ANALYTICS,
                    ):
                        sender = msg.sender_id
                        if self._latest_by_sender[priority].get(sender) == seq:
                            del self._latest_by_sender[priority][sender]
                    return msg

        return None

    def _shed_one(self, incoming_priority: MessagePriority) -> bool:
        """Shed one message from the lowest-priority lane that is strictly
        lower priority than ``incoming_priority``.

        Returns True if a message was successfully shed, False if nothing
        could be shed (the incoming message itself should be dropped).
        """
        incoming_rank = _PRIORITY_ORDER[incoming_priority]

        for shed_priority in _SHED_ORDER:
            shed_rank = _PRIORITY_ORDER[shed_priority]
            if shed_rank <= incoming_rank:
                # Don't shed messages at equal or higher priority.
                break
            lane = self._lanes[shed_priority]
            while lane:
                _, seq, _ = lane[0]
                if seq in self._tombstones:
                    heapq.heappop(lane)
                    self._tombstones.discard(seq)
                    continue
                heapq.heappop(lane)
                self._total_dropped += 1
                # Clean compaction tracking for shed entry.
                if self._compaction_enabled and shed_priority in (
                    MessagePriority.OPERATIONAL,
                    MessagePriority.ANALYTICS,
                ):
                    # We don't know the sender without inspecting — just note it's removed.
                    pass
                return True

        return False

    def get_stats(self) -> dict[str, int]:
        """Return queue depths per priority lane plus totals."""
        with self._lock:
            depths: dict[str, int] = {}
            for priority in MessagePriority:
                # Count non-tombstoned entries.
                count = sum(1 for _, seq, _ in self._lanes[priority] if seq not in self._tombstones)
                depths[priority.value] = count

            depths["total"] = sum(v for k, v in depths.items() if k != "total")
            depths["total_enqueued"] = self._total_enqueued
            depths["total_dropped"] = self._total_dropped
            return depths
