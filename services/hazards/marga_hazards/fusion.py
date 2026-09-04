"""Cooperative hazard fusion engine.

Turns noisy, per-source hazard observations into fused, confidence-aware
hazard objects with lifecycle management.

Key design choices
==================
* **Source independence**: 100 observations from the *same* source are worth
  far less than 10 from 10 independent sources.  Confidence tracks both
  raw evidence count and unique-source count.
* **Spatial association**: candidates are matched within an uncertainty
  radius; if two hazards share a road segment they must also match on it
  (prevents flyover/parallel-road merging).
* **Lifecycle**: CANDIDATE -> VERIFIED (on independent corroboration)
  -> STALE (on time decay) -> EXPIRED (on TTL or deep decay).
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timezone
from uuid import uuid4

from marga_schemas.common import GeoPoint
from marga_schemas.hazard import Hazard, HazardObservation, HazardState, HazardType

from .lifecycle import DEFAULT_TTL, HazardLifecycleManager
from .spatial import HazardSpatialIndex, haversine_m

logger = logging.getLogger(__name__)

# ── Fusion tunables ───────────────────────────────────────────────────
# Maximum distance (m) for two observations to be considered the same hazard
DEFAULT_ASSOCIATION_RADIUS_M: float = 50.0
# Maximum temporal gap (s) before time-gap factor drops to zero
MAX_TEMPORAL_GAP_S: float = 7200.0
# Minimum association score to merge
MIN_ASSOCIATION_SCORE: float = 0.3
# How many independent sources needed to promote CANDIDATE -> VERIFIED
VERIFICATION_SOURCE_COUNT: int = 2
# Cap on confidence contribution per repeated same-source observation
SAME_SOURCE_DIMINISHING_FACTOR: float = 0.05

# ── Type compatibility matrix ────────────────────────────────────────
# Some hazard types are "compatible" — e.g., a POTHOLE detected as BUMP
_COMPAT: dict[frozenset[HazardType], float] = {
    frozenset({HazardType.POTHOLE, HazardType.BUMP}): 0.6,
}


def _type_compatibility(a: HazardType, b: HazardType) -> float:
    """Return compatibility score in [0, 1] between two hazard types."""
    if a == b:
        return 1.0
    return _COMPAT.get(frozenset({a, b}), 0.0)


def _time_gap_factor(gap_s: float) -> float:
    """Return a [0, 1] factor that decays linearly with temporal gap."""
    if gap_s <= 0:
        return 1.0
    if gap_s >= MAX_TEMPORAL_GAP_S:
        return 0.0
    return 1.0 - (gap_s / MAX_TEMPORAL_GAP_S)


class HazardFusionEngine:
    """Core fusion engine.  Thread-safe for single-threaded async use."""

    def __init__(
        self,
        *,
        association_radius_m: float = DEFAULT_ASSOCIATION_RADIUS_M,
        min_association_score: float = MIN_ASSOCIATION_SCORE,
        verification_source_count: int = VERIFICATION_SOURCE_COUNT,
    ) -> None:
        self.association_radius_m = association_radius_m
        self.min_association_score = min_association_score
        self.verification_source_count = verification_source_count

        # Hazard store: hazard_id (str) -> Hazard
        self.hazards: dict[str, Hazard] = {}
        # Observation history: hazard_id -> list[HazardObservation]
        self.observation_history: dict[str, list[HazardObservation]] = {}

        self.spatial_index = HazardSpatialIndex()
        self.lifecycle = HazardLifecycleManager(self.hazards, self.spatial_index)

    # ------------------------------------------------------------------
    # Ingestion
    # ------------------------------------------------------------------

    def ingest_observation(self, obs: HazardObservation) -> Hazard:
        """Ingest a single observation, fuse it into the hazard store, and
        return the resulting (possibly new) fused hazard.
        """
        # Find best matching candidate in the spatial neighbourhood
        best_hazard, best_score = self._find_best_match(obs)

        if best_hazard is not None and best_score >= self.min_association_score:
            hazard = self._merge_observation(best_hazard, obs)
        else:
            hazard = self._create_hazard(obs)

        return hazard

    # ------------------------------------------------------------------
    # Negative evidence
    # ------------------------------------------------------------------

    def apply_negative_evidence(
        self,
        source_id: str,
        position: GeoPoint,
        hazard_type: HazardType,
        *,
        radius_m: float | None = None,
    ) -> list[Hazard]:
        """A trusted source asserts that a hazard no longer exists at *position*.

        Returns the list of affected hazards.
        """
        search_radius = radius_m or self.association_radius_m
        nearby = self.spatial_index.query_nearby(position, search_radius, hazard_type)
        affected: list[Hazard] = []

        for hazard in nearby:
            hid = str(hazard.hazard_id)
            hazard.contradiction_count += 1

            # Reduce confidence proportional to existing evidence
            # Each negative evidence knocks ~20% off confidence
            reduction = 0.2 * hazard.confidence
            hazard.confidence = max(0.0, hazard.confidence - reduction)

            # Possibly transition state
            if hazard.confidence < 0.1:
                hazard.state = HazardState.EXPIRED
                self.spatial_index.remove(hid)
                removed = self.hazards.pop(hid, None)
                if removed is not None:
                    self.lifecycle.expired_archive[hid] = removed
            elif hazard.confidence < 0.3:
                hazard.state = HazardState.STALE
                self.spatial_index.insert(hazard)
            else:
                self.spatial_index.insert(hazard)

            affected.append(hazard)
            logger.info(
                "Negative evidence from %s reduced hazard %s confidence to %.3f",
                source_id,
                hid,
                hazard.confidence,
            )

        return affected

    # ------------------------------------------------------------------
    # Query helpers
    # ------------------------------------------------------------------

    def get_hazard(self, hazard_id: str) -> Hazard | None:
        return self.hazards.get(hazard_id)

    def list_active_hazards(
        self,
        *,
        bbox: tuple[float, float, float, float] | None = None,
    ) -> list[Hazard]:
        """Return active hazards, optionally filtered by bounding box
        (min_lat, min_lon, max_lat, max_lon).
        """
        hazards = list(self.hazards.values())
        if bbox is not None:
            min_lat, min_lon, max_lat, max_lon = bbox
            hazards = [
                h
                for h in hazards
                if min_lat <= h.position.lat <= max_lat and min_lon <= h.position.lon <= max_lon
            ]
        return [h for h in hazards if h.state not in (HazardState.EXPIRED,)]

    # ------------------------------------------------------------------
    # Internal: matching
    # ------------------------------------------------------------------

    def _find_best_match(
        self, obs: HazardObservation
    ) -> tuple[Hazard | None, float]:
        """Find the best existing hazard to associate with *obs*."""
        candidates = self.spatial_index.query_nearby(
            obs.position,
            self.association_radius_m,
            hazard_type=None,  # check compatibility later
        )

        best: Hazard | None = None
        best_score: float = 0.0

        for hazard in candidates:
            score = self._association_score(hazard, obs)
            if score > best_score:
                best_score = score
                best = hazard

        return best, best_score

    def _association_score(self, hazard: Hazard, obs: HazardObservation) -> float:
        """Compute association score in [0, 1]."""
        # ── Type compatibility ─────────────────────────────────
        type_compat = _type_compatibility(hazard.hazard_type, obs.hazard_type)
        if type_compat == 0.0:
            return 0.0

        # ── Road segment compatibility ────────────────────────
        # If both specify a road segment, they must match
        if (
            hazard.road_segment_id is not None
            and obs.road_segment_id is not None
            and hazard.road_segment_id != obs.road_segment_id
        ):
            return 0.0

        # ── Spatial overlap ────────────────────────────────────
        dist = haversine_m(hazard.position, obs.position)
        if dist > self.association_radius_m:
            return 0.0
        spatial_score = 1.0 - (dist / self.association_radius_m)

        # ── Temporal gap ───────────────────────────────────────
        gap_s = abs((obs.observed_at - hazard.last_seen).total_seconds())
        time_score = _time_gap_factor(gap_s)

        # ── Source independence ────────────────────────────────
        # Slightly prefer merging with hazards already seen by other sources
        source_independent = obs.source_id not in hazard.source_ids
        independence_score = 1.0 if source_independent else 0.7

        return spatial_score * type_compat * time_score * independence_score

    # ------------------------------------------------------------------
    # Internal: creation & merging
    # ------------------------------------------------------------------

    def _create_hazard(self, obs: HazardObservation) -> Hazard:
        """Create a brand-new CANDIDATE hazard from a single observation."""
        hid = uuid4()
        ttl = DEFAULT_TTL.get(obs.hazard_type, 3_600)

        hazard = Hazard(
            hazard_id=hid,
            hazard_type=obs.hazard_type,
            position=obs.position.model_copy(),
            severity=obs.severity_hint,
            confidence=obs.detector_confidence * 0.5,  # single observation capped
            first_seen=obs.observed_at,
            last_seen=obs.observed_at,
            ttl_s=ttl,
            source_ids=[obs.source_id],
            evidence_count=1,
            state=HazardState.CANDIDATE,
            road_segment_id=obs.road_segment_id,
        )

        hid_str = str(hid)
        self.hazards[hid_str] = hazard
        self.observation_history[hid_str] = [obs]
        self.spatial_index.insert(hazard)

        logger.info(
            "Created CANDIDATE hazard %s (type=%s, confidence=%.3f)",
            hid_str,
            obs.hazard_type.value,
            hazard.confidence,
        )
        return hazard

    def _merge_observation(self, hazard: Hazard, obs: HazardObservation) -> Hazard:
        """Merge a new observation into an existing hazard, updating its
        confidence, position estimate, and state.
        """
        hid = str(hazard.hazard_id)
        history = self.observation_history.setdefault(hid, [])
        history.append(obs)

        # ── Update position (weighted average towards new observation) ─
        # Weight the new observation at 1/(n+1)
        n = hazard.evidence_count
        weight = 1.0 / (n + 1)
        hazard.position = GeoPoint(
            lat=hazard.position.lat * (1 - weight) + obs.position.lat * weight,
            lon=hazard.position.lon * (1 - weight) + obs.position.lon * weight,
        )

        # ── Update timestamps ─────────────────────────────────
        if obs.observed_at > hazard.last_seen:
            hazard.last_seen = obs.observed_at

        # ── Source tracking ────────────────────────────────────
        is_new_source = obs.source_id not in hazard.source_ids
        if is_new_source:
            hazard.source_ids.append(obs.source_id)

        hazard.evidence_count += 1

        # ── Confidence update ─────────────────────────────────
        hazard.confidence = self._compute_confidence(hazard, obs, is_new_source)

        # ── Severity update (weighted rolling) ─────────────────
        hazard.severity = hazard.severity * (1 - weight) + obs.severity_hint * weight

        # ── Road segment propagation ───────────────────────────
        if hazard.road_segment_id is None and obs.road_segment_id is not None:
            hazard.road_segment_id = obs.road_segment_id

        # ── State promotion ────────────────────────────────────
        unique_sources = len(hazard.source_ids)
        if (
            hazard.state == HazardState.CANDIDATE
            and unique_sources >= self.verification_source_count
        ):
            hazard.state = HazardState.VERIFIED
            logger.info("Hazard %s promoted to VERIFIED (%d sources)", hid, unique_sources)

        # Re-index with updated position
        self.spatial_index.insert(hazard)

        return hazard

    def _compute_confidence(
        self, hazard: Hazard, obs: HazardObservation, is_new_source: bool
    ) -> float:
        """Compute fused confidence.

        Independent corroboration (new sources) contributes much more than
        repeated observations from the same source.
        """
        base = hazard.confidence

        if is_new_source:
            # Independent corroboration — significant boost
            # Complement rule: P(A or B) = 1 - (1-P(A))*(1-P(B))
            new_evidence = obs.detector_confidence * 0.5
            combined = 1.0 - (1.0 - base) * (1.0 - new_evidence)
        else:
            # Same source — diminishing returns
            boost = obs.detector_confidence * SAME_SOURCE_DIMINISHING_FACTOR
            combined = base + boost * (1.0 - base)

        # Freshness bonus: recent observations slightly boost confidence
        # (already handled by resetting last_seen above, which resets decay)

        return min(1.0, max(0.0, combined))
