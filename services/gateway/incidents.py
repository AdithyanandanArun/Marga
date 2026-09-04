"""Bounded incident trace registry for explainable gateway decisions."""

from __future__ import annotations

from collections import deque
from typing import Any

from packages.schemas.canonical import RiskEvent


class IncidentTraceStore:
    """Store the full decision evidence for recently detected risks."""

    def __init__(self, limit: int = 10_000) -> None:
        self._items: dict[str, dict[str, Any]] = {}
        self._order: deque[str] = deque(maxlen=limit)

    def record_risk(self, risk: RiskEvent) -> dict[str, Any]:
        incident_id = risk.risk_id
        trace = {
            "incident_id": incident_id,
            "trace_id": incident_id,
            "decision_type": "risk_evaluation",
            "ts": risk.ts.isoformat(),
            "risk": risk.model_dump(mode="json"),
            "inputs": [{"entity_id": actor_id, "entity_type": "vehicle"} for actor_id in risk.affected_actor_ids],
            "derived_metrics": {
                "time_to_conflict_s": risk.time_to_conflict_s,
                "min_predicted_distance_m": risk.min_predicted_distance_m,
                "severity": risk.severity,
                "confidence": risk.confidence,
                "risk_score": risk.risk_score,
            },
            "evidence": risk.evidence,
            "policy_version": risk.policy_version,
        }
        if incident_id not in self._items and len(self._order) == self._order.maxlen:
            expired = self._order.popleft()
            self._items.pop(expired, None)
        if incident_id not in self._items:
            self._order.append(incident_id)
        self._items[incident_id] = trace
        return trace

    def get(self, incident_id: str) -> dict[str, Any] | None:
        return self._items.get(incident_id)


incident_traces = IncidentTraceStore()
