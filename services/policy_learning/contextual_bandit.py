"""A bounded contextual-bandit policy for V2X response selection.

Collision detection remains in the deterministic trajectory risk engine. This
learner only ranks *advisory delivery/response policies* from validated
outcomes; an explicit safety envelope overrides it for urgent cases.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from random import Random
from typing import Literal

PolicyAction = Literal["SLOW_DOWN_ADVISORY", "LOCAL_RELAY", "EARLY_WARNING"]


@dataclass(frozen=True, slots=True)
class PolicyContext:
    congestion_count: int
    gps_uncertainty_m: float
    connectivity: str
    risk_severity: float
    decision_key: str


class ContextualSafetyBandit:
    """Thompson-sampling learner with a mandatory safety envelope."""

    actions: tuple[PolicyAction, ...] = ("SLOW_DOWN_ADVISORY", "LOCAL_RELAY", "EARLY_WARNING")

    def __init__(self) -> None:
        self._posteriors: dict[tuple[str, PolicyAction], list[float]] = {}

    @staticmethod
    def _bucket(context: PolicyContext) -> str:
        density = "dense" if context.congestion_count >= 20 else "light"
        positioning = "uncertain" if context.gps_uncertainty_m >= 15 else "normal"
        return f"{density}:{positioning}:{context.connectivity}"

    def recommend(self, context: PolicyContext) -> dict[str, object]:
        # Safety-critical situations never explore: immediate slowing / local
        # radio delivery is selected by a deterministic, auditable envelope.
        if context.risk_severity >= 0.7:
            return self._result("SLOW_DOWN_ADVISORY", context, source="safety-envelope")
        if context.connectivity == "DIRECT_ONLY":
            return self._result("LOCAL_RELAY", context, source="safety-envelope")

        bucket = self._bucket(context)
        seed = int.from_bytes(sha256(f"{bucket}:{context.decision_key}".encode()).digest()[:8], "big")
        rng = Random(seed)
        samples = {
            action: rng.betavariate(*self._posteriors.get((bucket, action), [1.0, 1.0]))
            for action in self.actions
        }
        action = max(samples, key=samples.get)
        return self._result(action, context, source="contextual-bandit", sampled_utility=samples[action])

    def record_feedback(self, context: PolicyContext, action: PolicyAction, reward: float) -> dict[str, object]:
        if action not in self.actions:
            raise ValueError(f"unsupported action {action!r}")
        if not 0.0 <= reward <= 1.0:
            raise ValueError("reward must be in [0, 1]")
        posterior = self._posteriors.setdefault((self._bucket(context), action), [1.0, 1.0])
        posterior[0 if reward >= 0.5 else 1] += 1.0
        return {"bucket": self._bucket(context), "action": action, "alpha": posterior[0], "beta": posterior[1]}

    def _result(self, action: PolicyAction, context: PolicyContext, *, source: str, sampled_utility: float | None = None) -> dict[str, object]:
        bucket = self._bucket(context)
        alpha, beta = self._posteriors.get((bucket, action), [1.0, 1.0])
        return {
            "model": "contextual-bandit-v1",
            "advisory_only": True,
            "action": action,
            "source": source,
            "context_bucket": bucket,
            "estimated_success": round(alpha / (alpha + beta), 3),
            "sampled_utility": round(sampled_utility, 3) if sampled_utility is not None else None,
        }
