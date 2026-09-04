"""Tests for HazardFusionEngine.

Validates observation association, confidence computation with source
capping, negative evidence handling, time decay, lifecycle transitions,
and spatial matching.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from packages.safety_policies.config import PolicyConfig
from packages.schemas.canonical import HazardState, HazardType
from services.safety_detectors.detectors.hazard_fusion import HazardFusionEngine


def _make_observation(
    *,
    obs_type: str = "DEBRIS",
    lat: float = 12.9716,
    lon: float = 77.5946,
    source_id: str = "src-1",
    detector_confidence: float = 0.7,
    severity_hint: float = 0.5,
    is_negative: bool = False,
    road_segment_id: str | None = None,
    observed_at: datetime | None = None,
) -> dict[str, Any]:
    return {
        "type": obs_type,
        "geometry": {"type": "Point", "coordinates": [lon, lat]},
        "observed_at": (observed_at or datetime.now(timezone.utc)).isoformat(),
        "source_id": source_id,
        "detector_confidence": detector_confidence,
        "severity_hint": severity_hint,
        "is_negative": is_negative,
        "road_segment_id": road_segment_id,
    }


class TestHazardFusion:
    """Hazard fusion engine acceptance tests."""

    def test_new_observation_creates_candidate(self, policy_config: PolicyConfig) -> None:
        """A brand-new observation with no existing hazards should create a
        CANDIDATE hazard."""
        engine = HazardFusionEngine(policy_config)
        obs = _make_observation(obs_type="DEBRIS", source_id="src-1")
        hazard, action = engine.process_observation(obs, existing_hazards=[])
        assert action == "created"
        assert hazard["state"] == HazardState.CANDIDATE.value
        assert hazard["confidence"] > 0
        assert hazard["type"] == "DEBRIS"

    def test_corroboration_increases_confidence(self, policy_config: PolicyConfig) -> None:
        """Independent sources reporting the same hazard should increase confidence."""
        engine = HazardFusionEngine(policy_config)
        now = datetime.now(timezone.utc)

        obs1 = _make_observation(source_id="src-1", observed_at=now)
        h1, _ = engine.process_observation(obs1, existing_hazards=[])
        conf1 = h1["confidence"]

        obs2 = _make_observation(source_id="src-2", observed_at=now)
        h2, action = engine.process_observation(obs2, existing_hazards=[h1])
        conf2 = h2["confidence"]
        assert conf2 > conf1

        obs3 = _make_observation(source_id="src-3", observed_at=now)
        h3, _ = engine.process_observation(obs3, existing_hazards=[h2])
        conf3 = h3["confidence"]
        assert conf3 > conf2

    def test_single_source_capped(self, policy_config: PolicyConfig) -> None:
        """100 observations from ONE source should not equal 100 independent
        confirmations. Confidence should plateau."""
        engine = HazardFusionEngine(policy_config)
        now = datetime.now(timezone.utc)

        obs = _make_observation(source_id="src-same", observed_at=now)
        hazard, _ = engine.process_observation(obs, existing_hazards=[])
        conf_initial = hazard["confidence"]

        # Feed 20 more observations from the same source
        for _ in range(20):
            hazard, _ = engine.process_observation(
                _make_observation(source_id="src-same", observed_at=now),
                existing_hazards=[hazard],
            )
        conf_single = hazard["confidence"]

        # Now compare with independent sources
        engine2 = HazardFusionEngine(policy_config)
        hazard2, _ = engine2.process_observation(
            _make_observation(source_id="ind-0", observed_at=now),
            existing_hazards=[],
        )
        for i in range(1, 6):
            hazard2, _ = engine2.process_observation(
                _make_observation(source_id=f"ind-{i}", observed_at=now),
                existing_hazards=[hazard2],
            )
        conf_multi = hazard2["confidence"]

        # 6 independent sources should reach higher confidence than 21 from one
        assert conf_multi > conf_single

    def test_negative_evidence_reduces_confidence(self, policy_config: PolicyConfig) -> None:
        """Negative evidence from an observer should reduce hazard confidence."""
        engine = HazardFusionEngine(policy_config)
        now = datetime.now(timezone.utc)

        obs = _make_observation(source_id="src-1", observed_at=now, detector_confidence=0.8)
        hazard, _ = engine.process_observation(obs, existing_hazards=[])
        conf_before = hazard["confidence"]

        neg_obs = _make_observation(
            source_id="src-neg",
            observed_at=now,
            is_negative=True,
            detector_confidence=0.9,
        )
        hazard, _ = engine.process_observation(neg_obs, existing_hazards=[hazard])
        conf_after = hazard["confidence"]
        assert conf_after < conf_before

    def test_time_decay(self, policy_config: PolicyConfig) -> None:
        """Confidence should decay over time without fresh observations."""
        engine = HazardFusionEngine(policy_config)
        now = datetime.now(timezone.utc)

        obs = _make_observation(source_id="src-1", observed_at=now)
        hazard, _ = engine.process_observation(obs, existing_hazards=[])
        conf_fresh = hazard["confidence"]

        # Simulate time passing
        future = now + timedelta(minutes=10)
        decayed = engine.decay_hazards([hazard], future)
        assert len(decayed) > 0
        conf_decayed = decayed[0]["confidence"]
        assert conf_decayed < conf_fresh

    def test_lifecycle_transitions(self, policy_config: PolicyConfig) -> None:
        """Hazard should progress from CANDIDATE -> VERIFIED when confidence
        exceeds promotion threshold."""
        engine = HazardFusionEngine(policy_config)
        now = datetime.now(timezone.utc)

        # Create candidate with initial observation
        obs1 = _make_observation(source_id="src-1", observed_at=now, detector_confidence=0.4)
        hazard, _ = engine.process_observation(obs1, existing_hazards=[])
        assert hazard["state"] == HazardState.CANDIDATE.value

        # Corroborate from multiple independent sources to exceed promotion_confidence=0.7
        for i in range(2, 8):
            obs = _make_observation(
                source_id=f"src-{i}",
                observed_at=now,
                detector_confidence=0.7,
            )
            hazard, _ = engine.process_observation(obs, existing_hazards=[hazard])

        # Should be VERIFIED now
        assert hazard["state"] == HazardState.VERIFIED.value

    def test_spatial_matching(self, policy_config: PolicyConfig) -> None:
        """Observations close together should be associated with the same
        hazard. Distant observations should create separate hazards."""
        engine = HazardFusionEngine(policy_config)
        now = datetime.now(timezone.utc)

        obs_a = _make_observation(
            lat=12.9716, lon=77.5946, source_id="src-a", observed_at=now,
        )
        hazard_a, action_a = engine.process_observation(obs_a, existing_hazards=[])
        assert action_a == "created"

        # Very nearby observation -> should associate with existing
        obs_b = _make_observation(
            lat=12.9717, lon=77.5947, source_id="src-b", observed_at=now,
        )
        result, action_b = engine.process_observation(obs_b, existing_hazards=[hazard_a])
        assert action_b in ("updated", "unchanged")

        # Far-away observation -> should create new hazard
        obs_c = _make_observation(
            lat=13.0000, lon=77.6500, source_id="src-c", observed_at=now,
        )
        result_c, action_c = engine.process_observation(obs_c, existing_hazards=[hazard_a])
        assert action_c == "created"
        assert result_c["hazard_id"] != hazard_a["hazard_id"]
