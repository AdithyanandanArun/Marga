"""Hazard lifecycle management — TTL sweeper, confidence decay, state transitions.

Each hazard type has its own TTL and decay characteristics.  The lifecycle
manager runs periodic sweeps to transition hazards through:

    CANDIDATE -> VERIFIED -> STALE -> EXPIRED
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from marga_schemas.hazard import Hazard, HazardState, HazardType

from .spatial import HazardSpatialIndex

logger = logging.getLogger(__name__)

# ── Type-specific TTL defaults (seconds) ──────────────────────────────
DEFAULT_TTL: dict[HazardType, int] = {
    HazardType.POTHOLE: 86_400,         # 24 h — semi-permanent
    HazardType.BUMP: 604_800,           # 7 d  — infrastructure
    HazardType.DEBRIS: 3_600,           # 1 h  — typically cleared fast
    HazardType.FLOOD: 7_200,            # 2 h
    HazardType.LANDSLIDE: 86_400,       # 24 h
    HazardType.ANIMAL: 1_800,           # 30 min — highly transient
    HazardType.STALLED_VEHICLE: 3_600,  # 1 h
    HazardType.CONSTRUCTION: 604_800,   # 7 d
    HazardType.LANE_CLOSURE: 86_400,    # 24 h
    HazardType.ACCIDENT: 7_200,         # 2 h
    HazardType.LOW_VISIBILITY: 3_600,   # 1 h
    HazardType.OTHER: 3_600,            # 1 h
}

# ── Decay parameters ──────────────────────────────────────────────────
# Confidence halves every *half_life* seconds of inactivity
DEFAULT_DECAY_HALF_LIFE_S: float = 3_600.0  # 1 h

# Thresholds
STALE_CONFIDENCE_THRESHOLD: float = 0.3
EXPIRE_CONFIDENCE_THRESHOLD: float = 0.1


class HazardLifecycleManager:
    """Runs periodic sweeps over the active hazard store, decaying confidence
    and transitioning hazard state as needed.
    """

    def __init__(
        self,
        hazard_store: dict[str, Hazard],
        spatial_index: HazardSpatialIndex,
        *,
        ttl_overrides: dict[HazardType, int] | None = None,
        decay_half_life_s: float = DEFAULT_DECAY_HALF_LIFE_S,
        stale_threshold: float = STALE_CONFIDENCE_THRESHOLD,
        expire_threshold: float = EXPIRE_CONFIDENCE_THRESHOLD,
    ) -> None:
        self._store = hazard_store
        self._spatial = spatial_index
        self._ttl = {**DEFAULT_TTL, **(ttl_overrides or {})}
        self._decay_half_life = decay_half_life_s
        self._stale_threshold = stale_threshold
        self._expire_threshold = expire_threshold
        # Retain expired hazards for persistence/replay
        self.expired_archive: dict[str, Hazard] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def sweep(self, now: datetime | None = None) -> list[Hazard]:
        """Run one lifecycle sweep.  Returns list of hazards whose state changed."""
        now = now or datetime.now(timezone.utc)
        changed: list[Hazard] = []
        to_remove: list[str] = []

        for hid, hazard in list(self._store.items()):
            if hazard.state == HazardState.EXPIRED:
                continue

            # ── Confidence decay ──────────────────────────────────
            elapsed_s = (now - hazard.last_seen).total_seconds()
            if elapsed_s > 0 and self._decay_half_life > 0:
                decay_factor = 0.5 ** (elapsed_s / self._decay_half_life)
                new_confidence = hazard.confidence * decay_factor
            else:
                new_confidence = hazard.confidence

            # ── TTL check ─────────────────────────────────────────
            ttl = self._ttl.get(hazard.hazard_type, 3_600)
            age_s = (now - hazard.first_seen).total_seconds()
            ttl_exceeded = age_s > ttl

            # ── State transitions ─────────────────────────────────
            old_state = hazard.state

            if ttl_exceeded or new_confidence < self._expire_threshold:
                new_state = HazardState.EXPIRED
            elif new_confidence < self._stale_threshold:
                new_state = HazardState.STALE
            else:
                new_state = hazard.state  # keep current

            # Apply updates
            state_changed = new_state != old_state
            confidence_changed = abs(new_confidence - hazard.confidence) > 1e-6

            if state_changed or confidence_changed:
                hazard.confidence = max(0.0, new_confidence)
                hazard.state = new_state
                changed.append(hazard)

            if new_state == HazardState.EXPIRED:
                to_remove.append(hid)

        # Remove expired hazards from active store and spatial index
        for hid in to_remove:
            hazard = self._store.pop(hid)
            self._spatial.remove(hid)
            self.expired_archive[hid] = hazard
            logger.info("Hazard %s expired (type=%s)", hid, hazard.hazard_type.value)

        return changed

    def get_ttl(self, hazard_type: HazardType) -> int:
        """Return TTL in seconds for a given hazard type."""
        return self._ttl.get(hazard_type, 3_600)
