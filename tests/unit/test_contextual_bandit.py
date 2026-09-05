from services.policy_learning import AdaptiveSignalBandit, ContextualSafetyBandit, PolicyContext


def _context(**overrides: object) -> PolicyContext:
    values: dict[str, object] = {
        "congestion_count": 25,
        "gps_uncertainty_m": 4.0,
        "connectivity": "FULL",
        "risk_severity": 0.2,
        "decision_key": "decision-1",
    }
    values.update(overrides)
    return PolicyContext(**values)  # type: ignore[arg-type]


def test_imminent_risk_bypasses_learning_with_safe_action() -> None:
    result = ContextualSafetyBandit().recommend(_context(risk_severity=0.9))
    assert result["action"] == "SLOW_DOWN_ADVISORY"
    assert result["source"] == "safety-envelope"
    assert result["advisory_only"] is True


def test_direct_only_bypasses_learning_with_local_relay() -> None:
    result = ContextualSafetyBandit().recommend(_context(connectivity="DIRECT_ONLY"))
    assert result["action"] == "LOCAL_RELAY"
    assert result["source"] == "safety-envelope"


def test_feedback_updates_contextual_posterior() -> None:
    policy = ContextualSafetyBandit()
    result = policy.record_feedback(_context(), "EARLY_WARNING", 1.0)
    assert result["alpha"] == 2.0
    assert result["beta"] == 1.0


def test_signal_bandit_only_returns_prevalidated_green_durations() -> None:
    choice = AdaptiveSignalBandit().choose_green(ew_queue=14, ns_queue=8, movement="EW", decision_key="cycle-1")
    assert choice["duration_s"] in {18, 25, 32}
    assert choice["bounded"] is True
