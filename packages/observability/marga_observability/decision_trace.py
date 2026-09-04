"""Decision trace / audit trail for Marga risk and alert decisions.

Each decision is captured as a :class:`DecisionTrace` and buffered in
memory by :class:`DecisionTracer`. The buffer can be queried in-process
and is flushed to persistent storage asynchronously.
"""

from __future__ import annotations

import asyncio
import collections
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Coroutine
from uuid import UUID, uuid4


@dataclass(frozen=True, slots=True)
class DecisionInput:
    """Pointer to an entity version that participated in a decision."""

    entity_id: str
    version: str
    timestamp: datetime


@dataclass(slots=True)
class DecisionTrace:
    """Complete trace of a single decision evaluation.

    Attributes
    ----------
    decision_id : UUID
        Unique identifier for the decision.
    ts : datetime
        When the decision was made (UTC).
    decision_type : str
        Classification such as ``"risk_evaluation"``, ``"alert_issue"``.
    inputs : list[DecisionInput]
        Entity versions consumed by the decision logic.
    derived_metrics : dict
        Computed values: TTC, relative speed, confidence, etc.
    rules_fired : list[str]
        Names of rules/conditions that matched.
    output_ids : list[str]
        IDs of artifacts produced (alert IDs, risk IDs, etc.).
    trace_id : str | None
        OpenTelemetry trace id for cross-service correlation.
    """

    decision_id: UUID = field(default_factory=uuid4)
    ts: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    decision_type: str = ""
    inputs: list[DecisionInput] = field(default_factory=list)
    derived_metrics: dict[str, Any] = field(default_factory=dict)
    rules_fired: list[str] = field(default_factory=list)
    output_ids: list[str] = field(default_factory=list)
    trace_id: str | None = None


# Type alias for the async persistence callback.
PersistFn = Callable[[list[DecisionTrace]], Coroutine[Any, Any, None]]


class DecisionTracer:
    """In-memory decision trace buffer with optional async persistence.

    Parameters
    ----------
    max_buffer_size:
        Maximum number of traces held in memory before the oldest are
        evicted.
    persist_fn:
        Optional async callable invoked to flush traces to durable
        storage.  Signature: ``async def persist(traces: list[DecisionTrace]) -> None``.
    flush_threshold:
        When the buffer reaches this size ``persist_fn`` is called
        automatically.  Defaults to ``max_buffer_size``.
    """

    def __init__(
        self,
        *,
        max_buffer_size: int = 10_000,
        persist_fn: PersistFn | None = None,
        flush_threshold: int | None = None,
    ) -> None:
        self._buffer: collections.deque[DecisionTrace] = collections.deque(
            maxlen=max_buffer_size
        )
        self._index_by_id: dict[UUID, DecisionTrace] = {}
        self._persist_fn = persist_fn
        self._flush_threshold = flush_threshold or max_buffer_size
        self._lock = asyncio.Lock()

    async def record(self, trace: DecisionTrace) -> None:
        """Record a decision trace, optionally triggering a flush."""
        async with self._lock:
            self._buffer.append(trace)
            self._index_by_id[trace.decision_id] = trace

            # Evict from index if deque rotated out old entries
            if len(self._index_by_id) > len(self._buffer):
                indexed_ids = {t.decision_id for t in self._buffer}
                stale = set(self._index_by_id) - indexed_ids
                for sid in stale:
                    del self._index_by_id[sid]

        if self._persist_fn and len(self._buffer) >= self._flush_threshold:
            await self.flush()

    def get_trace(self, decision_id: UUID) -> DecisionTrace | None:
        """Look up a trace by decision ID (from in-memory buffer only)."""
        return self._index_by_id.get(decision_id)

    def query_traces(
        self,
        *,
        risk_id: str | None = None,
        actor_id: str | None = None,
        limit: int = 50,
    ) -> list[DecisionTrace]:
        """Query buffered traces with optional filters.

        ``risk_id`` and ``actor_id`` are matched against
        :attr:`DecisionTrace.output_ids` and :attr:`DecisionTrace.inputs`
        respectively.
        """
        results: list[DecisionTrace] = []
        for trace in reversed(self._buffer):
            if risk_id is not None and risk_id not in trace.output_ids:
                continue
            if actor_id is not None:
                actor_match = any(
                    inp.entity_id == actor_id for inp in trace.inputs
                )
                if not actor_match:
                    continue
            results.append(trace)
            if len(results) >= limit:
                break
        return results

    async def flush(self) -> None:
        """Persist buffered traces and clear the buffer."""
        if not self._persist_fn:
            return
        async with self._lock:
            batch = list(self._buffer)
            self._buffer.clear()
            self._index_by_id.clear()
        if batch:
            await self._persist_fn(batch)

    @property
    def buffer_size(self) -> int:
        return len(self._buffer)
