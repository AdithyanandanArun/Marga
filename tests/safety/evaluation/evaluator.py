"""Safety evaluation framework.

Provides a structured way to measure detector performance across
batches of labelled scenarios, computing precision, recall, and F1.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from packages.safety_policies.base import SafetyDetector
from packages.schemas.canonical import RiskEvent


@dataclass
class EvaluationReport:
    """Aggregated evaluation metrics for a detector."""

    detector_name: str
    total_scenarios: int = 0
    true_positives: int = 0
    false_positives: int = 0
    false_negatives: int = 0
    true_negatives: int = 0
    details: list[dict[str, Any]] = field(default_factory=list)

    @property
    def precision(self) -> float:
        denom = self.true_positives + self.false_positives
        return self.true_positives / denom if denom > 0 else 0.0

    @property
    def recall(self) -> float:
        denom = self.true_positives + self.false_negatives
        return self.true_positives / denom if denom > 0 else 0.0

    @property
    def f1_score(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) > 0 else 0.0

    def summary(self) -> dict[str, Any]:
        return {
            "detector": self.detector_name,
            "total_scenarios": self.total_scenarios,
            "true_positives": self.true_positives,
            "false_positives": self.false_positives,
            "false_negatives": self.false_negatives,
            "true_negatives": self.true_negatives,
            "precision": round(self.precision, 4),
            "recall": round(self.recall, 4),
            "f1_score": round(self.f1_score, 4),
        }


class SafetyEvaluator:
    """Run batches of labelled scenarios through a detector and measure performance."""

    @staticmethod
    def run_scenarios(
        detector: SafetyDetector,
        scenarios: list[dict[str, Any]],
    ) -> EvaluationReport:
        """Evaluate a detector against a list of labelled scenarios.

        Each scenario dict must contain:
            - world_state keys expected by the detector
            - "expect_risk" (bool): True if a risk should be detected
            - Optionally "vehicles_sequence" for multi-step scenarios

        Returns an EvaluationReport with full metrics.
        """
        report = EvaluationReport(detector_name=detector.name)

        for scenario in scenarios:
            report.total_scenarios += 1
            expect_risk = scenario.get("expect_risk", True)

            risks = SafetyEvaluator._run_single(detector, scenario)
            detected = len(risks) > 0

            detail: dict[str, Any] = {
                "expected": expect_risk,
                "detected": detected,
                "risk_count": len(risks),
            }

            if expect_risk and detected:
                report.true_positives += 1
                detail["result"] = "TP"
            elif expect_risk and not detected:
                report.false_negatives += 1
                detail["result"] = "FN"
            elif not expect_risk and detected:
                report.false_positives += 1
                detail["result"] = "FP"
            else:
                report.true_negatives += 1
                detail["result"] = "TN"

            report.details.append(detail)

        return report

    @staticmethod
    def _run_single(
        detector: SafetyDetector,
        scenario: dict[str, Any],
    ) -> list[RiskEvent]:
        """Run a single scenario through the detector."""
        if "vehicles_sequence" in scenario:
            all_risks: list[RiskEvent] = []
            for vehicles in scenario["vehicles_sequence"]:
                ws = {**scenario, "vehicles": vehicles}
                all_risks.extend(detector.evaluate(ws))
            return all_risks
        return detector.evaluate(scenario)
