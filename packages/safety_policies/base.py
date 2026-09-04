"""Base interfaces for safety detectors and policies.

Every safety feature module implements SafetyDetector. Policies define
threshold/configuration as versioned config, not duplicated core math.
"""

from __future__ import annotations

import abc
from datetime import datetime
from typing import Any

from packages.schemas.canonical import Alert, AlertLevel, RiskEvent, RiskType


class SafetyDetector(abc.ABC):
    """Abstract base for all safety feature detectors.

    A detector evaluates world state and produces RiskEvents when safety-
    relevant conditions are identified. Detectors must:
    - Operate on arbitrary valid inputs (no hard-coded coordinates/IDs)
    - Carry confidence and uncertainty through all decisions
    - Produce evidence sufficient to explain why a risk was detected
    """

    @property
    @abc.abstractmethod
    def name(self) -> str:
        """Unique detector name for logging and config."""

    @property
    @abc.abstractmethod
    def risk_type(self) -> RiskType:
        """The primary risk type this detector produces."""

    @property
    @abc.abstractmethod
    def version(self) -> str:
        """Policy/detector version string."""

    @abc.abstractmethod
    def evaluate(self, world_state: dict[str, Any]) -> list[RiskEvent]:
        """Evaluate current world state and return detected risks.

        Args:
            world_state: Dictionary containing current actor states, road
                network, hazards, signals, and any other relevant state.

        Returns:
            List of RiskEvent instances for detected safety risks.
        """

    def create_risk_event(
        self,
        *,
        affected_actor_ids: list[str],
        severity: float,
        confidence: float,
        evidence: list[dict[str, Any]],
        time_to_conflict_s: float | None = None,
        min_predicted_distance_m: float | None = None,
        road_segment_id: str | None = None,
        geometry: dict[str, Any] | None = None,
        ts: datetime | None = None,
        expires_at: datetime | None = None,
    ) -> RiskEvent:
        """Helper to create a RiskEvent with this detector's metadata."""
        from datetime import timezone

        now = ts or datetime.now(timezone.utc)
        risk_score = severity * confidence
        return RiskEvent(
            type=self.risk_type,
            ts=now,
            affected_actor_ids=affected_actor_ids,
            time_to_conflict_s=time_to_conflict_s,
            min_predicted_distance_m=min_predicted_distance_m,
            severity=severity,
            confidence=confidence,
            risk_score=risk_score,
            evidence=evidence,
            expires_at=expires_at,
            geometry=geometry,
            road_segment_id=road_segment_id,
            policy_version=self.version,
        )


class SafetyPolicy(abc.ABC):
    """Abstract base for policies that convert RiskEvents into Alerts.

    Policies define the mapping from risk assessment to human-facing
    warnings with prioritization, suppression, and lifecycle management.
    """

    @property
    @abc.abstractmethod
    def name(self) -> str:
        """Unique policy name."""

    @property
    @abc.abstractmethod
    def version(self) -> str:
        """Policy version string."""

    @abc.abstractmethod
    def evaluate_risk(self, risk: RiskEvent, context: dict[str, Any]) -> Alert | None:
        """Convert a RiskEvent into an Alert (or None if suppressed).

        Args:
            risk: The detected risk event.
            context: Additional context (active alerts, actor states, etc.)

        Returns:
            An Alert if the risk warrants one, None if suppressed.
        """

    @abc.abstractmethod
    def should_suppress(self, risk: RiskEvent, active_alerts: list[Alert]) -> bool:
        """Determine if this risk should be suppressed given active alerts.

        Prevents duplicate/redundant alerting for the same ongoing risk.
        """

    def determine_level(self, risk: RiskEvent) -> AlertLevel:
        """Map risk score to alert level. Override for custom thresholds."""
        if risk.risk_score >= 0.8:
            return AlertLevel.CRITICAL
        elif risk.risk_score >= 0.5:
            return AlertLevel.WARNING
        elif risk.risk_score >= 0.2:
            return AlertLevel.ADVISORY
        return AlertLevel.INFORMATIONAL
