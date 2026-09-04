"""Alert prioritization engine.

Scores and ranks alerts by urgency so that the most time-critical,
high-severity warnings reach actors first.  Scoring factors and their
weights are fully configurable at construction time.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from marga_schemas.alert import Alert, AlertPriority

# ---------------------------------------------------------------------------
# Default weight set – tuned so a critical collision with low time-to-conflict
# always outranks an informational road-hazard notice.
# ---------------------------------------------------------------------------
DEFAULT_WEIGHTS: dict[str, float] = {
    "time_to_conflict": 0.30,
    "collision_severity": 0.20,
    "confidence": 0.15,
    "braking_feasibility": 0.10,
    "actor_vulnerability": 0.10,
    "message_age": 0.10,
    "duplication_state": 0.05,
}

# Map AlertPriority → base multiplier so that priority enum alone
# provides coarse ordering before fine-grained scoring kicks in.
_PRIORITY_BASE: dict[AlertPriority, float] = {
    AlertPriority.CRITICAL: 100.0,
    AlertPriority.HIGH: 75.0,
    AlertPriority.MEDIUM: 50.0,
    AlertPriority.LOW: 25.0,
    AlertPriority.INFO: 10.0,
}


@dataclass
class AlertPrioritizer:
    """Score and rank a batch of alerts by actionable urgency."""

    weights: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_WEIGHTS))

    # ---- public API -------------------------------------------------------

    def prioritize(self, alerts: list[Alert]) -> list[Alert]:
        """Return *alerts* sorted highest-priority-first by computed score."""
        scored = [(self.compute_score(a), a) for a in alerts]
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [a for _, a in scored]

    def compute_score(self, alert: Alert) -> float:
        """Return a numeric priority score for *alert*.

        The score is the sum of the priority-class base value and a
        weighted combination of machine-reasoning factors stored on the
        alert's ``machine_reasoning`` dict.

        Missing factors are treated as neutral (0.5 on a 0-1 scale) so
        that alerts with partial evidence still receive a usable ranking.
        """
        mr = alert.machine_reasoning
        base = _PRIORITY_BASE.get(alert.priority, 50.0)

        # --- extract factors, defaulting to neutral -----------------------
        time_to_conflict = self._invert(mr.get("time_to_conflict_s"))
        collision_severity = float(mr.get("collision_severity", 0.5))
        confidence = alert.confidence
        braking_feasibility = self._invert_feasibility(mr.get("braking_feasibility"))
        actor_vulnerability = float(mr.get("actor_vulnerability", 0.5))
        message_age = self._message_age_score(alert)
        duplication_state = 0.0 if mr.get("is_duplicate") else 1.0

        weighted = (
            self.weights["time_to_conflict"] * time_to_conflict
            + self.weights["collision_severity"] * collision_severity
            + self.weights["confidence"] * confidence
            + self.weights["braking_feasibility"] * braking_feasibility
            + self.weights["actor_vulnerability"] * actor_vulnerability
            + self.weights["message_age"] * message_age
            + self.weights["duplication_state"] * duplication_state
        )

        # weighted is in [0, 1]; scale to [0, 100] and add base.
        return base + weighted * 100.0

    # ---- internal helpers -------------------------------------------------

    @staticmethod
    def _invert(time_to_conflict_s: float | None) -> float:
        """Shorter time-to-conflict ⇒ higher urgency (closer to 1.0).

        Uses a simple reciprocal mapping clipped to [0, 1].
        A *None* value is treated as neutral (0.5).
        """
        if time_to_conflict_s is None:
            return 0.5
        ttc = float(time_to_conflict_s)
        if ttc <= 0:
            return 1.0
        # 5 s is the reference "very dangerous" threshold
        return min(1.0, 5.0 / (ttc + 5.0) * 2.0)

    @staticmethod
    def _invert_feasibility(braking_feasibility: float | None) -> float:
        """Lower braking feasibility ⇒ higher urgency.

        Feasibility 1.0 means the actor can easily stop; 0.0 means no
        chance.  We invert so that harder-to-avoid situations score higher.
        """
        if braking_feasibility is None:
            return 0.5
        return 1.0 - float(braking_feasibility)

    @staticmethod
    def _message_age_score(alert: Alert) -> float:
        """Newer alerts score higher.  Alerts older than 60 s score 0."""
        age_s = (datetime.now(UTC) - alert.created_at.replace(tzinfo=UTC)).total_seconds()
        if age_s <= 0:
            return 1.0
        if age_s >= 60:
            return 0.0
        return 1.0 - (age_s / 60.0)
