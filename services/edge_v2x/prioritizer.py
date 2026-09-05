"""Risk prioritizer for edge V2X nodes — selects one active risk.

Per the final implementation blueprint, each edge node must prioritise
one active risk using:
    - collision probability
    - time-to-collision (TTC)
    - uncertainty
    - consequence (severity)
    - road-user vulnerability

The prioritizer takes a list of RiskEvents detected by the local
EdgeRiskEvaluator and returns the single most important one, plus a
human-facing alert description that does not claim certainty beyond
evidence.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from packages.schemas.canonical import RiskEvent, RiskType

logger = logging.getLogger(__name__)

POLICY_VERSION = "edge-prioritizer-v1"


@dataclass(frozen=True, slots=True)
class PrioritizationFactors:
    """Decomposed factors that drive the priority score."""

    collision_probability: float
    ttc_urgency: float
    uncertainty_penalty: float
    consequence: float
    vulnerability: float
    composite_score: float


def _ttc_urgency(ttc_s: float | None, horizon_s: float = 8.0) -> float:
    """Convert TTC to urgency [0, 1].  Lower TTC = higher urgency."""
    if ttc_s is None:
        return 0.0
    if ttc_s <= 0:
        return 1.0
    return max(0.0, 1.0 - ttc_s / horizon_s)


def _uncertainty_penalty(confidence: float) -> float:
    """Convert confidence to uncertainty penalty [0, 1].

    High confidence -> low penalty.  Low confidence -> high penalty.
    """
    return 1.0 - confidence


class RiskPrioritizer:
    """Select the single most important risk for a driver.

    The composite score is a weighted blend of:
        30% collision probability (risk_score)
        25% TTC urgency
        15% uncertainty penalty (inverted — lower uncertainty is better)
        20% consequence (severity)
        10% road-user vulnerability

    The highest-scoring risk is returned as the active risk.  If no risks
    are detected, None is returned.
    """

    def __init__(
        self,
        *,
        w_probability: float = 0.30,
        w_ttc: float = 0.25,
        w_uncertainty: float = 0.15,
        w_consequence: float = 0.20,
        w_vulnerability: float = 0.10,
        horizon_s: float = 8.0,
    ) -> None:
        self.weights = {
            "probability": w_probability,
            "ttc": w_ttc,
            "uncertainty": w_uncertainty,
            "consequence": w_consequence,
            "vulnerability": w_vulnerability,
        }
        self.horizon_s = horizon_s

    def compute_factors(self, risk: RiskEvent) -> PrioritizationFactors:
        """Decompose a risk event into prioritization factors."""
        collision_probability = risk.risk_score
        ttc_urg = _ttc_urgency(risk.time_to_conflict_s, self.horizon_s)
        unc_penalty = _uncertainty_penalty(risk.confidence)
        consequence = risk.severity

        # Vulnerability: use the max vulnerability from evidence, or default.
        vulnerability = self._extract_vulnerability(risk)

        composite = (
            self.weights["probability"] * collision_probability
            + self.weights["ttc"] * ttc_urg
            + self.weights["uncertainty"] * (1.0 - unc_penalty)
            + self.weights["consequence"] * consequence
            + self.weights["vulnerability"] * vulnerability
        )
        composite = max(0.0, min(1.0, composite))

        return PrioritizationFactors(
            collision_probability=round(collision_probability, 4),
            ttc_urgency=round(ttc_urg, 4),
            uncertainty_penalty=round(unc_penalty, 4),
            consequence=round(consequence, 4),
            vulnerability=round(vulnerability, 4),
            composite_score=round(composite, 4),
        )

    def prioritize(self, risks: list[RiskEvent]) -> RiskEvent | None:
        """Select the single highest-priority risk.

        Returns the RiskEvent with the highest composite score, or None
        if the list is empty.
        """
        if not risks:
            return None

        best_risk: RiskEvent | None = None
        best_score = -1.0

        for risk in risks:
            factors = self.compute_factors(risk)
            if factors.composite_score > best_score:
                best_score = factors.composite_score
                best_risk = risk

        return best_risk

    def prioritize_with_factors(
        self, risks: list[RiskEvent]
    ) -> tuple[RiskEvent | None, PrioritizationFactors | None]:
        """Select the highest-priority risk and return its decomposition."""
        best = self.prioritize(risks)
        if best is None:
            return None, None
        return best, self.compute_factors(best)

    def driver_text(self, risk: RiskEvent, factors: PrioritizationFactors) -> str:
        """Generate concise driver-facing text.

        Per the spec: "UI wording must not claim certainty beyond evidence."
        Uses hedged language: "Possible", "Potential", "may be".
        """
        type_label = self._risk_type_label(risk.type)
        ttc_str = ""
        if risk.time_to_conflict_s is not None and risk.time_to_conflict_s > 0:
            ttc_str = f" in {risk.time_to_conflict_s:.1f}s"

        confidence_label = "low confidence" if risk.confidence < 0.5 else "moderate confidence"
        if risk.confidence >= 0.8:
            confidence_label = "high confidence"

        return f"Possible {type_label}{ttc_str} ({confidence_label})"

    def machine_reasoning(
        self, risk: RiskEvent, factors: PrioritizationFactors
    ) -> dict[str, float | str | None]:
        """Generate machine reasoning trace for explainability.

        Stored separately from driver-facing text per the spec.
        """
        return {
            "policy_version": POLICY_VERSION,
            "composite_score": factors.composite_score,
            "collision_probability": factors.collision_probability,
            "ttc_urgency": factors.ttc_urgency,
            "uncertainty_penalty": factors.uncertainty_penalty,
            "consequence": factors.consequence,
            "vulnerability": factors.vulnerability,
            "risk_type": risk.type.value,
            "ttc_s": risk.time_to_conflict_s,
            "severity": risk.severity,
            "confidence": risk.confidence,
        }

    def _extract_vulnerability(self, risk: RiskEvent) -> float:
        """Extract max vulnerability from risk evidence."""
        max_v = 0.40  # default
        for evidence in risk.evidence:
            v = evidence.get("max_vulnerability")
            if v is not None and v > max_v:
                max_v = v
        return max_v

    def _risk_type_label(self, risk_type: RiskType) -> str:
        """Human-readable label for risk type."""
        labels = {
            RiskType.HEAD_ON: "head-on collision",
            RiskType.REAR_END: "rear-end collision",
            RiskType.INTERSECTION_CONFLICT: "intersection conflict",
            RiskType.COLLISION: "collision",
            RiskType.EMERGENCY_BRAKING: "emergency braking ahead",
            RiskType.PEDESTRIAN_CONFLICT: "pedestrian conflict",
            RiskType.ANIMAL_CROSSING: "animal on road",
            RiskType.WRONG_WAY: "wrong-way vehicle",
            RiskType.STALLED_VEHICLE: "stalled vehicle ahead",
            RiskType.ROAD_HAZARD: "road hazard",
            RiskType.BLIND_CURVE: "blind curve conflict",
            RiskType.BLIND_INTERSECTION: "blind intersection conflict",
            RiskType.EMERGENCY_VEHICLE: "emergency vehicle approaching",
            RiskType.ROAD_NARROWING: "road narrowing ahead",
        }
        return labels.get(risk_type, "safety risk")
