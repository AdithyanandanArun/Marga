"""Tests for the Marga Hazard Fusion engine, lifecycle, and spatial index."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from marga_schemas.common import GeoPoint
from marga_schemas.hazard import Hazard, HazardObservation, HazardState, HazardType

from services.hazards.marga_hazards.fusion import HazardFusionEngine
from services.hazards.marga_hazards.lifecycle import (
    DEFAULT_TTL,
)
from services.hazards.marga_hazards.spatial import HazardSpatialIndex, haversine_m

# ── Helpers ───────────────────────────────────────────────────────────

_NOW = datetime(2025, 6, 15, 12, 0, 0, tzinfo=UTC)

# A point in central Bangalore (~12.97, 77.59)
_POS_A = GeoPoint(lat=12.9716, lon=77.5946)
# ~30 m away from _POS_A
_POS_A_NEAR = GeoPoint(lat=12.9719, lon=77.5946)
# ~200 m away — beyond default 50 m association radius
_POS_B = GeoPoint(lat=12.9735, lon=77.5946)
# Different city entirely
_POS_C = GeoPoint(lat=19.0760, lon=72.8777)


def _make_obs(
    *,
    hazard_type: HazardType = HazardType.POTHOLE,
    position: GeoPoint = _POS_A,
    source_id: str = "phone-001",
    detector_confidence: float = 0.8,
    observed_at: datetime = _NOW,
    road_segment_id: str | None = None,
    severity_hint: float = 0.5,
) -> HazardObservation:
    return HazardObservation(
        observation_id=uuid4(),
        hazard_type=hazard_type,
        position=position,
        observed_at=observed_at,
        source_id=source_id,
        detector_confidence=detector_confidence,
        severity_hint=severity_hint,
        road_segment_id=road_segment_id,
    )


# ══════════════════════════════════════════════════════════════════════
# Spatial index tests
# ══════════════════════════════════════════════════════════════════════


class TestHaversine:
    def test_same_point_is_zero(self) -> None:
        assert haversine_m(_POS_A, _POS_A) == 0.0

    def test_known_distance(self) -> None:
        # Bangalore to Mumbai — approx 842 km
        dist = haversine_m(_POS_A, _POS_C)
        assert 830_000 < dist < 860_000

    def test_nearby_points(self) -> None:
        dist = haversine_m(_POS_A, _POS_A_NEAR)
        assert 20 < dist < 50  # ~33 m


class TestSpatialIndex:
    def test_insert_and_query(self) -> None:
        idx = HazardSpatialIndex()
        h = Hazard(
            hazard_type=HazardType.POTHOLE,
            position=_POS_A,
            severity=0.5,
            confidence=0.5,
            first_seen=_NOW,
            last_seen=_NOW,
            ttl_s=3600,
        )
        idx.insert(h)
        assert len(idx) == 1

        results = idx.query_nearby(_POS_A, 10.0)
        assert len(results) == 1
        assert results[0].hazard_id == h.hazard_id

    def test_query_respects_radius(self) -> None:
        idx = HazardSpatialIndex()
        h = Hazard(
            hazard_type=HazardType.POTHOLE,
            position=_POS_A,
            severity=0.5,
            confidence=0.5,
            first_seen=_NOW,
            last_seen=_NOW,
            ttl_s=3600,
        )
        idx.insert(h)

        # _POS_B is ~200 m away — should not appear within 50 m radius
        results = idx.query_nearby(_POS_B, 50.0)
        assert len(results) == 0

    def test_query_filters_by_type(self) -> None:
        idx = HazardSpatialIndex()
        h1 = Hazard(
            hazard_type=HazardType.POTHOLE,
            position=_POS_A,
            severity=0.5,
            confidence=0.5,
            first_seen=_NOW,
            last_seen=_NOW,
            ttl_s=3600,
        )
        h2 = Hazard(
            hazard_type=HazardType.DEBRIS,
            position=_POS_A_NEAR,
            severity=0.5,
            confidence=0.5,
            first_seen=_NOW,
            last_seen=_NOW,
            ttl_s=3600,
        )
        idx.insert(h1)
        idx.insert(h2)

        results = idx.query_nearby(_POS_A, 100.0, hazard_type=HazardType.POTHOLE)
        assert len(results) == 1
        assert results[0].hazard_type == HazardType.POTHOLE

    def test_remove(self) -> None:
        idx = HazardSpatialIndex()
        h = Hazard(
            hazard_type=HazardType.POTHOLE,
            position=_POS_A,
            severity=0.5,
            confidence=0.5,
            first_seen=_NOW,
            last_seen=_NOW,
            ttl_s=3600,
        )
        idx.insert(h)
        idx.remove(str(h.hazard_id))
        assert len(idx) == 0
        assert idx.query_nearby(_POS_A, 100.0) == []


# ══════════════════════════════════════════════════════════════════════
# Fusion engine tests
# ══════════════════════════════════════════════════════════════════════


class TestNewObservationCreatesCandidate:
    def test_single_obs_creates_candidate(self) -> None:
        engine = HazardFusionEngine()
        obs = _make_obs()
        hazard = engine.ingest_observation(obs)

        assert hazard.state == HazardState.CANDIDATE
        assert hazard.evidence_count == 1
        assert hazard.source_ids == ["phone-001"]
        assert 0 < hazard.confidence < 1

    def test_candidate_confidence_is_capped(self) -> None:
        engine = HazardFusionEngine()
        obs = _make_obs(detector_confidence=1.0)
        hazard = engine.ingest_observation(obs)

        # Single observation should not yield confidence=1.0
        assert hazard.confidence <= 0.6


class TestIndependentCorroboration:
    def test_two_sources_promote_to_verified(self) -> None:
        engine = HazardFusionEngine()
        obs1 = _make_obs(source_id="phone-001")
        obs2 = _make_obs(
            source_id="camera-002",
            position=_POS_A_NEAR,
            observed_at=_NOW + timedelta(seconds=30),
        )

        engine.ingest_observation(obs1)
        hazard = engine.ingest_observation(obs2)

        assert hazard.state == HazardState.VERIFIED
        assert hazard.evidence_count == 2
        assert set(hazard.source_ids) == {"phone-001", "camera-002"}

    def test_three_independent_sources_higher_confidence(self) -> None:
        engine = HazardFusionEngine()
        h1 = engine.ingest_observation(_make_obs(source_id="s1"))
        conf_1 = h1.confidence

        h2 = engine.ingest_observation(_make_obs(source_id="s2", observed_at=_NOW + timedelta(seconds=10)))
        conf_2 = h2.confidence

        h3 = engine.ingest_observation(_make_obs(source_id="s3", observed_at=_NOW + timedelta(seconds=20)))
        conf_3 = h3.confidence

        # All merged into the same hazard
        assert h1.hazard_id == h2.hazard_id == h3.hazard_id
        assert h3.evidence_count == 3
        # Each independent source should monotonically increase confidence
        assert conf_3 > conf_2 > conf_1


class TestSameSourceDiminishingReturns:
    def test_same_source_doesnt_inflate_equally(self) -> None:
        engine = HazardFusionEngine()
        obs1 = _make_obs(source_id="phone-001")
        h1 = engine.ingest_observation(obs1)
        conf_after_1 = h1.confidence

        # 5 more observations from the SAME source
        for i in range(5):
            obs = _make_obs(
                source_id="phone-001",
                observed_at=_NOW + timedelta(seconds=10 * (i + 1)),
            )
            h1 = engine.ingest_observation(obs)

        conf_after_6 = h1.confidence

        # Now compare with 1 observation from a NEW source
        engine2 = HazardFusionEngine()
        engine2.ingest_observation(_make_obs(source_id="phone-001"))
        h_with_new = engine2.ingest_observation(
            _make_obs(
                source_id="camera-002",
                observed_at=_NOW + timedelta(seconds=10),
            )
        )

        # A single new-source observation should boost more than 5 same-source
        # observations, demonstrating diminishing returns
        same_source_boost = conf_after_6 - conf_after_1
        new_source_boost = h_with_new.confidence - conf_after_1

        assert new_source_boost > same_source_boost


class TestSpatialProximityMatching:
    def test_nearby_observations_merge(self) -> None:
        engine = HazardFusionEngine()
        h1 = engine.ingest_observation(_make_obs(position=_POS_A))
        h2 = engine.ingest_observation(
            _make_obs(
                position=_POS_A_NEAR,
                source_id="camera-002",
                observed_at=_NOW + timedelta(seconds=5),
            )
        )

        # Should have merged into same hazard
        assert h1.hazard_id == h2.hazard_id

    def test_distant_observations_separate(self) -> None:
        engine = HazardFusionEngine()
        h1 = engine.ingest_observation(_make_obs(position=_POS_A))
        h2 = engine.ingest_observation(
            _make_obs(
                position=_POS_B,  # ~200 m away
                source_id="camera-002",
                observed_at=_NOW + timedelta(seconds=5),
            )
        )

        # Should be separate hazards
        assert h1.hazard_id != h2.hazard_id
        assert len(engine.hazards) == 2


class TestDifferentTypesDontMerge:
    def test_incompatible_types_stay_separate(self) -> None:
        engine = HazardFusionEngine()
        h1 = engine.ingest_observation(_make_obs(hazard_type=HazardType.POTHOLE, position=_POS_A))
        h2 = engine.ingest_observation(
            _make_obs(
                hazard_type=HazardType.FLOOD,
                position=_POS_A_NEAR,
                source_id="camera-002",
                observed_at=_NOW + timedelta(seconds=5),
            )
        )

        assert h1.hazard_id != h2.hazard_id

    def test_compatible_types_can_merge(self) -> None:
        """POTHOLE and BUMP are considered compatible — they merge when
        spatially very close (the 0.6 compat factor requires high spatial
        overlap to exceed the 0.3 association threshold).
        """
        engine = HazardFusionEngine()
        # Use positions ~5 m apart so spatial_score ~ 0.9
        pos_very_near = GeoPoint(lat=12.97164, lon=77.5946)
        h1 = engine.ingest_observation(_make_obs(hazard_type=HazardType.POTHOLE, position=_POS_A))
        h2 = engine.ingest_observation(
            _make_obs(
                hazard_type=HazardType.BUMP,
                position=pos_very_near,
                source_id="camera-002",
                observed_at=_NOW + timedelta(seconds=5),
            )
        )

        # 0.9 spatial * 0.6 type_compat * ~1.0 time * 1.0 independence > 0.3
        assert h1.hazard_id == h2.hazard_id


class TestRoadSegmentIsolation:
    def test_same_segment_merges(self) -> None:
        engine = HazardFusionEngine()
        h1 = engine.ingest_observation(_make_obs(road_segment_id="seg-101", position=_POS_A))
        h2 = engine.ingest_observation(
            _make_obs(
                road_segment_id="seg-101",
                position=_POS_A_NEAR,
                source_id="s2",
                observed_at=_NOW + timedelta(seconds=5),
            )
        )
        assert h1.hazard_id == h2.hazard_id

    def test_different_segment_no_merge(self) -> None:
        """Parallel road / flyover — different road_segment_id prevents merge."""
        engine = HazardFusionEngine()
        h1 = engine.ingest_observation(_make_obs(road_segment_id="seg-101", position=_POS_A))
        h2 = engine.ingest_observation(
            _make_obs(
                road_segment_id="seg-202",  # different segment (e.g. flyover)
                position=_POS_A_NEAR,
                source_id="s2",
                observed_at=_NOW + timedelta(seconds=5),
            )
        )
        assert h1.hazard_id != h2.hazard_id

    def test_one_segment_null_allows_merge(self) -> None:
        """If one observation has no road segment, merging is allowed."""
        engine = HazardFusionEngine()
        h1 = engine.ingest_observation(_make_obs(road_segment_id="seg-101", position=_POS_A))
        h2 = engine.ingest_observation(
            _make_obs(
                road_segment_id=None,
                position=_POS_A_NEAR,
                source_id="s2",
                observed_at=_NOW + timedelta(seconds=5),
            )
        )
        assert h1.hazard_id == h2.hazard_id


# ══════════════════════════════════════════════════════════════════════
# Lifecycle / TTL tests
# ══════════════════════════════════════════════════════════════════════


class TestConfidenceDecay:
    def test_confidence_decays_over_time(self) -> None:
        engine = HazardFusionEngine()
        obs = _make_obs(observed_at=_NOW)
        hazard = engine.ingest_observation(obs)
        initial_conf = hazard.confidence

        # Sweep 2 hours later — confidence should have decayed
        later = _NOW + timedelta(hours=2)
        changed = engine.lifecycle.sweep(now=later)

        assert len(changed) >= 1
        assert hazard.confidence < initial_conf

    def test_stale_transition_on_low_confidence(self) -> None:
        engine = HazardFusionEngine()
        obs = _make_obs(observed_at=_NOW, detector_confidence=0.6)
        hazard = engine.ingest_observation(obs)

        # Sweep far enough into the future for confidence to drop below stale threshold
        future = _NOW + timedelta(hours=4)
        engine.lifecycle.sweep(now=future)

        assert hazard.state in (HazardState.STALE, HazardState.EXPIRED)


class TestTTLExpiration:
    def test_debris_expires_after_ttl(self) -> None:
        engine = HazardFusionEngine()
        obs = _make_obs(hazard_type=HazardType.DEBRIS, observed_at=_NOW)
        hazard = engine.ingest_observation(obs)
        hid = str(hazard.hazard_id)

        ttl = DEFAULT_TTL[HazardType.DEBRIS]  # 3600 s
        future = _NOW + timedelta(seconds=ttl + 100)
        engine.lifecycle.sweep(now=future)

        # Should have been removed from active store
        assert hid not in engine.hazards
        # But retained in archive
        assert hid in engine.lifecycle.expired_archive

    def test_pothole_longer_lived_than_debris(self) -> None:
        assert DEFAULT_TTL[HazardType.POTHOLE] > DEFAULT_TTL[HazardType.DEBRIS]
        assert DEFAULT_TTL[HazardType.BUMP] > DEFAULT_TTL[HazardType.ANIMAL]


# ══════════════════════════════════════════════════════════════════════
# Negative evidence tests
# ══════════════════════════════════════════════════════════════════════


class TestNegativeEvidence:
    def test_negative_evidence_reduces_confidence(self) -> None:
        engine = HazardFusionEngine()
        obs = _make_obs(observed_at=_NOW)
        hazard = engine.ingest_observation(obs)
        initial_conf = hazard.confidence

        affected = engine.apply_negative_evidence(
            source_id="authority-001",
            position=_POS_A,
            hazard_type=HazardType.POTHOLE,
        )

        assert len(affected) == 1
        assert affected[0].confidence < initial_conf
        assert affected[0].contradiction_count == 1

    def test_repeated_negative_evidence_expires_hazard(self) -> None:
        engine = HazardFusionEngine()
        obs = _make_obs(observed_at=_NOW, detector_confidence=0.5)
        hazard = engine.ingest_observation(obs)
        hid = str(hazard.hazard_id)

        # Apply many rounds of negative evidence
        for i in range(20):
            engine.apply_negative_evidence(
                source_id=f"authority-{i:03d}",
                position=_POS_A,
                hazard_type=HazardType.POTHOLE,
            )

        # Eventually the hazard should be expired and removed
        assert hid not in engine.hazards

    def test_negative_evidence_wrong_type_no_effect(self) -> None:
        engine = HazardFusionEngine()
        obs = _make_obs(hazard_type=HazardType.POTHOLE, observed_at=_NOW)
        hazard = engine.ingest_observation(obs)
        initial_conf = hazard.confidence

        # Negative evidence for a different type at same position
        affected = engine.apply_negative_evidence(
            source_id="authority-001",
            position=_POS_A,
            hazard_type=HazardType.FLOOD,
        )

        assert len(affected) == 0
        assert hazard.confidence == initial_conf


# ══════════════════════════════════════════════════════════════════════
# Integration-style tests
# ══════════════════════════════════════════════════════════════════════


class TestEndToEndLifecycle:
    def test_full_lifecycle_candidate_to_expired(self) -> None:
        engine = HazardFusionEngine()

        # Step 1: Single observation -> CANDIDATE
        obs1 = _make_obs(source_id="s1", observed_at=_NOW)
        h = engine.ingest_observation(obs1)
        assert h.state == HazardState.CANDIDATE

        # Step 2: Second independent source -> VERIFIED
        obs2 = _make_obs(
            source_id="s2",
            position=_POS_A_NEAR,
            observed_at=_NOW + timedelta(seconds=30),
        )
        h = engine.ingest_observation(obs2)
        assert h.state == HazardState.VERIFIED

        # Step 3: Time passes -> STALE
        stale_time = _NOW + timedelta(hours=3)
        engine.lifecycle.sweep(now=stale_time)
        assert h.state in (HazardState.STALE, HazardState.EXPIRED)

        # Step 4: More time passes -> EXPIRED
        expire_time = _NOW + timedelta(days=2)
        engine.lifecycle.sweep(now=expire_time)
        hid = str(h.hazard_id)
        assert hid not in engine.hazards or h.state == HazardState.EXPIRED

    def test_bbox_filter(self) -> None:
        engine = HazardFusionEngine()
        engine.ingest_observation(_make_obs(position=_POS_A))
        engine.ingest_observation(
            _make_obs(
                position=_POS_C,  # Mumbai
                source_id="s2",
                observed_at=_NOW + timedelta(seconds=5),
            )
        )

        # BBox around Bangalore only
        results = engine.list_active_hazards(bbox=(12.0, 77.0, 13.0, 78.0))
        assert len(results) == 1
        assert results[0].position.lat == pytest.approx(_POS_A.lat, abs=0.01)

    def test_observation_history_tracked(self) -> None:
        engine = HazardFusionEngine()
        obs1 = _make_obs(source_id="s1")
        h = engine.ingest_observation(obs1)
        hid = str(h.hazard_id)

        obs2 = _make_obs(
            source_id="s2",
            position=_POS_A_NEAR,
            observed_at=_NOW + timedelta(seconds=10),
        )
        engine.ingest_observation(obs2)

        history = engine.observation_history[hid]
        assert len(history) == 2
        assert history[0].source_id == "s1"
        assert history[1].source_id == "s2"
