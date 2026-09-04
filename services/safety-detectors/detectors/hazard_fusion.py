"""Cooperative hazard fusion engine (Playbook 9 / Section 27).

Fuses hazard observations from multiple independent sources into a
shared hazard map with proper confidence accounting, source-weight
capping, negative-evidence handling, and lifecycle management.

Design invariants
-----------------
* 100 repeated reports from ONE source must never equal 100 independent
  confirmations.  Per-source contribution is capped by
  ``max_source_weight`` so that only truly independent sources can
  drive confidence toward 1.0.
* Negative evidence (``is_negative=True``) from a trusted observer that
  actually traversed the location accelerates clearance of temporary
  hazards (debris, flooding, etc.).
* Lifecycle: CANDIDATE -> VERIFIED -> STALE -> EXPIRED.  Promotion
  requires confidence >= ``promotion_confidence``.  Staleness triggers
  after ``stale_threshold_s`` without any supporting observation.
* ``hazard.updated`` is conceptually published only when a material
  state change occurs (state transition, geometry shift, or significant
  confidence delta).
"""

from __future__ import annotations

import math
import uuid
from datetime import datetime, timezone
from typing import Any

from packages.geo.helpers import haversine_distance
from packages.safety_policies.config import HazardFusionConfig, PolicyConfig
from packages.schemas.canonical import HazardState, HazardType

_VERSION = "0.1.0"

# Confidence change smaller than this is not considered material.
_CONFIDENCE_MATERIAL_DELTA = 0.05

# Geometry shift smaller than this (meters) is not considered material.
_GEOMETRY_MATERIAL_SHIFT_M = 5.0

# Multiplier applied to negative-evidence decay of confidence.
_NEGATIVE_EVIDENCE_FACTOR = 0.25

# Minimum confidence floor; below this a hazard expires outright.
_MIN_CONFIDENCE = 0.01

# Compatibility matrix: observation type -> set of compatible hazard types.
# Types not listed are only compatible with themselves.
_TYPE_COMPAT: dict[str, set[str]] = {
    HazardType.DEBRIS.value: {
        HazardType.DEBRIS.value,
        HazardType.ACCIDENT.value,
    },
    HazardType.ACCIDENT.value: {
        HazardType.ACCIDENT.value,
        HazardType.DEBRIS.value,
        HazardType.LANE_CLOSURE.value,
    },
    HazardType.LANE_CLOSURE.value: {
        HazardType.LANE_CLOSURE.value,
        HazardType.CONSTRUCTION.value,
        HazardType.ACCIDENT.value,
    },
    HazardType.CONSTRUCTION.value: {
        HazardType.CONSTRUCTION.value,
        HazardType.LANE_CLOSURE.value,
    },
    HazardType.FLOOD.value: {
        HazardType.FLOOD.value,
        HazardType.LOW_VISIBILITY.value,
    },
    HazardType.LOW_VISIBILITY.value: {
        HazardType.LOW_VISIBILITY.value,
        HazardType.FLOOD.value,
    },
}

# Temporary hazard types whose clearance is accelerated by negative evidence.
_TEMPORARY_TYPES: set[str] = {
    HazardType.DEBRIS.value,
    HazardType.FLOOD.value,
    HazardType.ANIMAL.value,
    HazardType.ACCIDENT.value,
    HazardType.LOW_VISIBILITY.value,
}

# Fallback TTL map (mirrors RoadHazardConfig defaults so the engine can
# function independently of the detector config at runtime).
_DEFAULT_TTL: dict[str, int] = {
    "POTHOLE": 86400,
    "BUMP": 86400,
    "DEBRIS": 3600,
    "FLOOD": 7200,
    "CONSTRUCTION": 86400,
    "LANE_CLOSURE": 86400,
    "ACCIDENT": 3600,
    "LOW_VISIBILITY": 1800,
    "ROAD_NARROWING": 86400,
    "OTHER": 3600,
}


# ------------------------------------------------------------------
# Utility helpers
# ------------------------------------------------------------------

def _to_str(value: str | HazardType) -> str:
    return value.value if isinstance(value, HazardType) else value


def _to_state_str(value: str | HazardState) -> str:
    return value.value if isinstance(value, HazardState) else value


def _parse_dt(value: str | datetime) -> datetime:
    """Parse an ISO-8601 string or pass through a datetime, always UTC."""
    if isinstance(value, str):
        dt = datetime.fromisoformat(value)
    else:
        dt = value
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _extract_point(geometry: dict[str, Any]) -> tuple[float, float] | None:
    """Return (lat, lon) from a GeoJSON geometry or *None*."""
    geom_type = geometry.get("type")
    coords = geometry.get("coordinates")
    if coords is None:
        return None
    if geom_type == "Point":
        return coords[1], coords[0]
    if geom_type == "LineString":
        if not coords:
            return None
        lats = [c[1] for c in coords]
        lons = [c[0] for c in coords]
        return sum(lats) / len(lats), sum(lons) / len(lons)
    if geom_type == "Polygon":
        ring = coords[0] if coords else []
        if not ring:
            return None
        lats = [c[1] for c in ring]
        lons = [c[0] for c in ring]
        return sum(lats) / len(lats), sum(lons) / len(lons)
    return None


def _types_compatible(obs_type: str, candidate_type: str) -> bool:
    """Check whether an observation type is compatible with a candidate."""
    if obs_type == candidate_type:
        return True
    compat = _TYPE_COMPAT.get(obs_type)
    return bool(compat and candidate_type in compat)


# ------------------------------------------------------------------
# Engine
# ------------------------------------------------------------------

class HazardFusionEngine:
    """Fuse independent hazard observations into a canonical hazard map.

    This is **not** a ``SafetyDetector``; it operates upstream,
    maintaining the shared hazard state that detectors later consume.
    """

    def __init__(self, config: PolicyConfig) -> None:
        self._cfg: HazardFusionConfig = config.hazard_fusion
        # Pull TTL map from road_hazard config for type-specific expiry.
        self._ttl_map: dict[str, int] = dict(config.road_hazard.default_ttl_s)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def process_observation(
        self,
        observation: dict[str, Any],
        existing_hazards: list[dict[str, Any]],
    ) -> tuple[dict[str, Any], str]:
        """Fuse a single observation into the hazard list.

        Returns
        -------
        (hazard_dict, action)
            *hazard_dict* is the created or updated hazard.
            *action* is ``"created"``, ``"updated"``, or ``"unchanged"``.
        """
        # -- Step 1: validate observation basics --------------------------
        obs_type = _to_str(observation.get("type", "OTHER"))
        obs_geometry = observation.get("geometry", {})
        obs_point = _extract_point(obs_geometry)
        if obs_point is None:
            raise ValueError("Observation geometry must resolve to a point")

        observed_at = _parse_dt(observation["observed_at"])
        source_id: str = observation["source_id"]
        detector_confidence: float = float(
            observation.get("detector_confidence", 0.5),
        )
        severity_hint: float = float(observation.get("severity_hint", 0.5))
        is_negative: bool = bool(observation.get("is_negative", False))
        road_segment_id: str | None = observation.get("road_segment_id")

        # -- Step 2: find best matching candidate -------------------------
        best_candidate: dict[str, Any] | None = None
        best_score: float = 0.0

        for hazard in existing_hazards:
            h_state = _to_state_str(
                hazard.get("state", HazardState.CANDIDATE.value),
            )
            if h_state == HazardState.EXPIRED.value:
                continue

            h_type = _to_str(hazard.get("type", "OTHER"))
            if not _types_compatible(obs_type, h_type):
                continue

            score = self.compute_association_score(observation, hazard)
            if score > best_score:
                best_score = score
                best_candidate = hazard

        # -- Step 3/4: associate or create --------------------------------
        if (
            best_candidate is not None
            and best_score >= self._cfg.association_score_threshold
        ):
            return self._update_hazard(
                best_candidate,
                observation,
                obs_type,
                obs_point,
                observed_at,
                source_id,
                detector_confidence,
                severity_hint,
                is_negative,
                road_segment_id,
            )

        # No viable candidate -- create a new CANDIDATE hazard.
        new_hazard = self._create_hazard(
            observation,
            obs_type,
            obs_geometry,
            observed_at,
            source_id,
            detector_confidence,
            severity_hint,
            road_segment_id,
        )
        return new_hazard, "created"

    def compute_association_score(
        self,
        observation: dict[str, Any],
        candidate: dict[str, Any],
    ) -> float:
        """Compute an association score in [0, 1].

        Factors
        -------
        * Spatial proximity (inverse distance, uncertainty-aware radius)
        * Same road segment bonus
        * Type compatibility
        * Temporal recency (smaller time gap -> higher score)
        * Source independence bonus
        """
        obs_point = _extract_point(observation.get("geometry", {}))
        cand_point = _extract_point(candidate.get("geometry", {}))
        if obs_point is None or cand_point is None:
            return 0.0

        # -- spatial proximity -------------------------------------------
        distance_m = haversine_distance(
            obs_point[0], obs_point[1], cand_point[0], cand_point[1],
        )
        radius = self._cfg.spatial_match_radius_m
        if distance_m > radius:
            return 0.0
        spatial_score = 1.0 - (distance_m / radius)

        # -- type compatibility ------------------------------------------
        obs_type = _to_str(observation.get("type", "OTHER"))
        cand_type = _to_str(candidate.get("type", "OTHER"))
        if obs_type == cand_type:
            type_score = 1.0
        elif _types_compatible(obs_type, cand_type):
            type_score = 0.7
        else:
            return 0.0

        # -- temporal recency --------------------------------------------
        try:
            obs_time = _parse_dt(observation["observed_at"])
            cand_last = _parse_dt(
                candidate.get(
                    "last_seen",
                    candidate.get("first_seen", observation["observed_at"]),
                ),
            )
            gap_s = abs((obs_time - cand_last).total_seconds())
        except (KeyError, TypeError, ValueError):
            gap_s = 0.0
        # Decay over a 1-hour window.
        time_score = math.exp(-gap_s / 3600.0)

        # -- same road segment -------------------------------------------
        obs_seg = observation.get("road_segment_id")
        cand_seg = candidate.get("road_segment_id")
        segment_bonus = (
            0.15 if (obs_seg and cand_seg and obs_seg == cand_seg) else 0.0
        )

        # -- source independence -----------------------------------------
        source_id = observation.get("source_id")
        existing_sources: list[str] = candidate.get("source_ids", [])
        independence_bonus = (
            0.1 if (source_id and source_id not in existing_sources) else 0.0
        )

        raw = (
            0.45 * spatial_score
            + 0.25 * type_score
            + 0.15 * time_score
            + segment_bonus
            + independence_bonus
        )
        return min(1.0, max(0.0, raw))

    def decay_hazards(
        self,
        hazards: list[dict[str, Any]],
        current_time_utc: datetime,
    ) -> list[dict[str, Any]]:
        """Apply time-based confidence decay and state transitions.

        Returns the updated list.  Expired hazards whose TTL has been
        exceeded by 2x are removed; others are kept briefly for callers
        that may need the record.
        """
        result: list[dict[str, Any]] = []
        for hazard in hazards:
            hazard = self._decay_single(hazard, current_time_utc)
            state = _to_state_str(
                hazard.get("state", HazardState.CANDIDATE.value),
            )
            if state != HazardState.EXPIRED.value:
                result.append(hazard)
            else:
                first_seen = _parse_dt(
                    hazard.get("first_seen", current_time_utc.isoformat()),
                )
                h_type = _to_str(hazard.get("type", "OTHER"))
                ttl = hazard.get("ttl_s") or self._ttl_map.get(
                    h_type, _DEFAULT_TTL.get(h_type, 3600),
                )
                if (current_time_utc - first_seen).total_seconds() <= ttl * 2:
                    result.append(hazard)
        return result

    # ------------------------------------------------------------------
    # Private: creation
    # ------------------------------------------------------------------

    def _create_hazard(
        self,
        observation: dict[str, Any],
        obs_type: str,
        obs_geometry: dict[str, Any],
        observed_at: datetime,
        source_id: str,
        detector_confidence: float,
        severity_hint: float,
        road_segment_id: str | None,
    ) -> dict[str, Any]:
        """Mint a new CANDIDATE hazard from a single observation."""
        ttl_s = self._ttl_map.get(
            obs_type, _DEFAULT_TTL.get(obs_type, 3600),
        )
        return {
            "hazard_id": str(uuid.uuid4()),
            "type": obs_type,
            "geometry": obs_geometry,
            "severity": min(1.0, max(0.0, severity_hint)),
            "confidence": min(1.0, max(0.0, detector_confidence)),
            "first_seen": observed_at.isoformat(),
            "last_seen": observed_at.isoformat(),
            "ttl_s": ttl_s,
            "source_ids": [source_id],
            "evidence_count": 1,
            "state": HazardState.CANDIDATE.value,
            "road_segment_id": road_segment_id,
            "_source_contributions": {source_id: 1},
        }

    # ------------------------------------------------------------------
    # Private: update / evidence integration
    # ------------------------------------------------------------------

    def _update_hazard(
        self,
        hazard: dict[str, Any],
        observation: dict[str, Any],
        obs_type: str,
        obs_point: tuple[float, float],
        observed_at: datetime,
        source_id: str,
        detector_confidence: float,
        severity_hint: float,
        is_negative: bool,
        road_segment_id: str | None,
    ) -> tuple[dict[str, Any], str]:
        """Integrate an observation into an existing hazard.

        Returns ``(updated_hazard, action)`` where *action* is
        ``"updated"`` or ``"unchanged"`` depending on whether a material
        change occurred.
        """
        old_confidence = float(hazard.get("confidence", 0.0))
        old_state = _to_state_str(
            hazard.get("state", HazardState.CANDIDATE.value),
        )
        old_geometry = hazard.get("geometry", {})

        # -- Track per-source observation count --------------------------
        contributions: dict[str, int] = dict(
            hazard.get("_source_contributions", {}),
        )
        contributions[source_id] = contributions.get(source_id, 0) + 1
        hazard["_source_contributions"] = contributions

        # -- Branch: negative vs positive evidence -----------------------
        if is_negative:
            self._apply_negative_evidence(
                hazard, obs_type, source_id, detector_confidence,
            )
        else:
            hazard["evidence_count"] = hazard.get("evidence_count", 0) + 1
            if source_id not in hazard.get("source_ids", []):
                source_ids = list(hazard.get("source_ids", []))
                source_ids.append(source_id)
                hazard["source_ids"] = source_ids

            new_confidence = self._recompute_confidence(
                hazard, detector_confidence, source_id,
            )
            hazard["confidence"] = new_confidence

            # Weighted-average geometry shift toward new observation.
            hazard["geometry"] = self._update_geometry(
                old_geometry, obs_point, new_confidence,
            )

            # Severity: take maximum of existing and hint.
            hazard["severity"] = min(
                1.0,
                max(float(hazard.get("severity", 0.0)), severity_hint),
            )

            hazard["last_seen"] = observed_at.isoformat()

            if road_segment_id:
                hazard["road_segment_id"] = road_segment_id

        # -- Lifecycle transition ----------------------------------------
        new_state = self._transition_state(hazard)
        hazard["state"] = new_state

        # -- Material-change detection -----------------------------------
        new_confidence = float(hazard.get("confidence", 0.0))
        confidence_changed = (
            abs(new_confidence - old_confidence) >= _CONFIDENCE_MATERIAL_DELTA
        )
        state_changed = new_state != old_state

        geom_shifted = False
        new_point = _extract_point(hazard.get("geometry", {}))
        old_point = _extract_point(old_geometry)
        if new_point and old_point:
            shift = haversine_distance(
                old_point[0], old_point[1], new_point[0], new_point[1],
            )
            geom_shifted = shift >= _GEOMETRY_MATERIAL_SHIFT_M

        action = (
            "updated"
            if (confidence_changed or state_changed or geom_shifted)
            else "unchanged"
        )
        return hazard, action

    def _recompute_confidence(
        self,
        hazard: dict[str, Any],
        new_detector_confidence: float,
        source_id: str,
    ) -> float:
        """Recompute hazard confidence with source-weight capping.

        Key invariant: repeated observations from a single source yield
        diminishing returns.  Only the *first* observation from a source
        applies the full ``new_detector_confidence``; subsequent ones
        are down-weighted by ``max_source_weight / obs_count``.  This
        ensures 100 reports from one source never equal 100 independent
        confirmations.

        The update is incremental (Bayesian-style move toward 1.0)
        so that independent sources stack effectively.
        """
        base = float(hazard.get("confidence", 0.0))
        contributions: dict[str, int] = hazard.get(
            "_source_contributions", {},
        )
        source_obs_count = contributions.get(source_id, 1)

        if source_obs_count <= 1:
            # First observation from this source -- full contribution.
            weight = 1.0
        else:
            # Repeated observations -- diminishing, capped contribution.
            weight = self._cfg.max_source_weight / source_obs_count

        # Move confidence toward 1.0 proportionally.
        increment = new_detector_confidence * weight * (1.0 - base)
        return min(1.0, base + increment)

    def _apply_negative_evidence(
        self,
        hazard: dict[str, Any],
        obs_type: str,
        source_id: str,
        detector_confidence: float,
    ) -> None:
        """Apply negative evidence -- observer says hazard is not present.

        Temporary hazard types (debris, flooding, etc.) decay faster
        when an observer has actually traversed the location.
        """
        h_type = _to_str(hazard.get("type", "OTHER"))
        current_confidence = float(hazard.get("confidence", 0.0))

        if h_type in _TEMPORARY_TYPES:
            decay = _NEGATIVE_EVIDENCE_FACTOR * detector_confidence * 2.0
        else:
            decay = _NEGATIVE_EVIDENCE_FACTOR * detector_confidence

        hazard["confidence"] = max(0.0, current_confidence - decay)

    def _update_geometry(
        self,
        old_geometry: dict[str, Any],
        new_point: tuple[float, float],
        weight: float,
    ) -> dict[str, Any]:
        """Weighted-average geometry update toward new observation point."""
        old_point = _extract_point(old_geometry)
        if old_point is None or old_geometry.get("type") != "Point":
            return {
                "type": "Point",
                "coordinates": [new_point[1], new_point[0]],
            }

        alpha = min(0.3, weight * 0.2)
        new_lat = old_point[0] + alpha * (new_point[0] - old_point[0])
        new_lon = old_point[1] + alpha * (new_point[1] - old_point[1])
        return {"type": "Point", "coordinates": [new_lon, new_lat]}

    def _transition_state(self, hazard: dict[str, Any]) -> str:
        """Determine the correct lifecycle state for a hazard."""
        confidence = float(hazard.get("confidence", 0.0))
        current = _to_state_str(
            hazard.get("state", HazardState.CANDIDATE.value),
        )

        if confidence < _MIN_CONFIDENCE:
            return HazardState.EXPIRED.value

        # Once expired, stay expired.
        if current == HazardState.EXPIRED.value:
            return HazardState.EXPIRED.value

        if confidence >= self._cfg.promotion_confidence:
            return HazardState.VERIFIED.value

        # Verified hazard whose confidence dropped below promotion
        # threshold becomes STALE rather than regressing to CANDIDATE.
        if current == HazardState.VERIFIED.value:
            return HazardState.STALE.value

        # Stale hazard can recover if confidence is restored.
        if current == HazardState.STALE.value:
            if confidence >= self._cfg.promotion_confidence:
                return HazardState.VERIFIED.value
            return HazardState.STALE.value

        return current

    # ------------------------------------------------------------------
    # Private: decay
    # ------------------------------------------------------------------

    def _decay_single(
        self,
        hazard: dict[str, Any],
        now: datetime,
    ) -> dict[str, Any]:
        """Apply time decay to a single hazard and transition states."""
        last_seen = _parse_dt(
            hazard.get("last_seen", hazard.get("first_seen", now.isoformat())),
        )
        gap_s = (now - last_seen).total_seconds()

        if gap_s <= 0:
            return hazard

        current_state = _to_state_str(
            hazard.get("state", HazardState.CANDIDATE.value),
        )
        if current_state == HazardState.EXPIRED.value:
            return hazard

        # -- Confidence decay (exponential) ------------------------------
        current_confidence = float(hazard.get("confidence", 0.0))
        decayed = current_confidence * math.exp(
            -self._cfg.confidence_decay_rate * gap_s,
        )
        hazard["confidence"] = max(0.0, decayed)

        # -- Staleness: no observation for stale_threshold_s -------------
        if (
            gap_s >= self._cfg.stale_threshold_s
            and current_state not in (
                HazardState.STALE.value,
                HazardState.EXPIRED.value,
            )
        ):
            hazard["state"] = HazardState.STALE.value

        # -- TTL-based expiry -------------------------------------------
        first_seen = _parse_dt(
            hazard.get("first_seen", now.isoformat()),
        )
        h_type = _to_str(hazard.get("type", "OTHER"))
        ttl = hazard.get("ttl_s") or self._ttl_map.get(
            h_type, _DEFAULT_TTL.get(h_type, 3600),
        )
        age_s = (now - first_seen).total_seconds()
        if age_s > ttl:
            hazard["state"] = HazardState.EXPIRED.value
            hazard["confidence"] = 0.0

        # -- Confidence floor expiry ------------------------------------
        if hazard["confidence"] < _MIN_CONFIDENCE:
            hazard["state"] = HazardState.EXPIRED.value
            hazard["confidence"] = 0.0

        # -- Re-run lifecycle for intermediate states --------------------
        if hazard["state"] != HazardState.EXPIRED.value:
            hazard["state"] = self._transition_state(hazard)

        return hazard
