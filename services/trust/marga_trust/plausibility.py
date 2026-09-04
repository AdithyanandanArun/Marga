"""Motion and location plausibility checking for V2X actor updates."""

from __future__ import annotations

import logging
import math
import threading
from dataclasses import dataclass
from datetime import datetime, timezone

from marga_schemas.common import ActorType

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------
# Physical limits by actor type (m/s for speed, m/s^2 for accel)
# ---------------------------------------------------------------
_MAX_SPEED: dict[ActorType, float] = {
    ActorType.PEDESTRIAN: 12.0,     # sprinting
    ActorType.ANIMAL: 25.0,
    ActorType.BIKE: 30.0,
    ActorType.AUTO: 30.0,           # auto-rickshaw
    ActorType.CAR: 70.0,            # ~250 km/h
    ActorType.BUS: 40.0,
    ActorType.TRUCK: 35.0,
    ActorType.AMBULANCE: 55.0,
    ActorType.OTHER: 70.0,
}

_MAX_ACCEL: dict[ActorType, float] = {
    ActorType.PEDESTRIAN: 5.0,
    ActorType.ANIMAL: 15.0,
    ActorType.BIKE: 8.0,
    ActorType.AUTO: 6.0,
    ActorType.CAR: 15.0,
    ActorType.BUS: 5.0,
    ActorType.TRUCK: 4.0,
    ActorType.AMBULANCE: 12.0,
    ActorType.OTHER: 15.0,
}

# Earth radius for Haversine (metres).
_EARTH_RADIUS_M = 6_371_000.0


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two points in metres."""
    rlat1, rlat2 = math.radians(lat1), math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(rlat1) * math.cos(rlat2) * math.sin(dlon / 2) ** 2
    return 2.0 * _EARTH_RADIUS_M * math.asin(math.sqrt(a))


@dataclass
class _ActorSnapshot:
    """Last known kinematic state for an actor."""

    lat: float
    lon: float
    speed_mps: float
    timestamp: datetime
    road_segment_id: str | None = None


class PlausibilityChecker:
    """Detect impossible motion — teleportation, impossible speed/accel, backwards time.

    Each call to :meth:`check` updates the internal state for the actor and
    returns a plausibility score in ``[0, 1]`` together with a list of
    anomaly tags.  A score of ``1.0`` means fully plausible; ``0.0`` means
    certainly spoofed.

    Scoring is multiplicative: each check contributes a factor in ``[0, 1]``,
    and they are multiplied together.
    """

    def __init__(self, *, teleport_threshold_m: float = 5_000.0) -> None:
        self._state: dict[str, _ActorSnapshot] = {}
        self._lock = threading.Lock()
        self._teleport_threshold_m = teleport_threshold_m

    def check(
        self,
        actor_id: str,
        lat: float,
        lon: float,
        speed_mps: float,
        timestamp: datetime,
        actor_type: ActorType = ActorType.CAR,
        road_segment_id: str | None = None,
    ) -> tuple[float, list[str]]:
        """Run plausibility checks and return ``(score, anomalies)``.

        *score* is in ``[0.0, 1.0]``.  *anomalies* is a list of human-readable
        tags such as ``TELEPORTATION``, ``IMPOSSIBLE_SPEED``, etc.
        """
        anomalies: list[str] = []
        score = 1.0

        # Ensure timestamp is offset-aware (assume UTC if naive).
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)

        # --- Speed sanity (independent of history) ---
        max_speed = _MAX_SPEED.get(actor_type, _MAX_SPEED[ActorType.OTHER])
        if speed_mps > max_speed:
            ratio = max_speed / speed_mps if speed_mps > 0 else 0.0
            score *= max(ratio, 0.1)
            anomalies.append("IMPOSSIBLE_SPEED")

        if speed_mps < 0:
            score *= 0.0
            anomalies.append("NEGATIVE_SPEED")

        with self._lock:
            prev = self._state.get(actor_id)

            if prev is not None:
                prev_ts = prev.timestamp if prev.timestamp.tzinfo else prev.timestamp.replace(tzinfo=timezone.utc)

                dt = (timestamp - prev_ts).total_seconds()

                # --- Backwards timestamp ---
                if dt < 0:
                    score *= 0.0
                    anomalies.append("BACKWARDS_TIMESTAMP")
                    # Do NOT update state — keep the newer record.
                    return score, anomalies

                if dt > 0:
                    dist = _haversine_m(prev.lat, prev.lon, lat, lon)

                    # --- Teleportation ---
                    if dist > self._teleport_threshold_m:
                        score *= 0.0
                        anomalies.append("TELEPORTATION")
                    else:
                        # Implied speed.
                        implied_speed = dist / dt
                        if implied_speed > max_speed * 1.5:
                            ratio = max_speed / implied_speed
                            score *= max(ratio, 0.1)
                            anomalies.append("IMPLAUSIBLE_JUMP")

                    # --- Acceleration ---
                    if dt > 0:
                        accel = abs(speed_mps - prev.speed_mps) / dt
                        max_accel = _MAX_ACCEL.get(actor_type, _MAX_ACCEL[ActorType.OTHER])
                        if accel > max_accel * 2:
                            ratio = max_accel / accel
                            score *= max(ratio, 0.1)
                            anomalies.append("IMPOSSIBLE_ACCELERATION")

                    # --- Road segment continuity (simple check) ---
                    if (
                        road_segment_id is not None
                        and prev.road_segment_id is not None
                        and road_segment_id != prev.road_segment_id
                        and dt < 1.0
                        and dist > 500
                    ):
                        score *= 0.5
                        anomalies.append("SEGMENT_DISCONTINUITY")

            # Update actor state with the latest observation.
            self._state[actor_id] = _ActorSnapshot(
                lat=lat,
                lon=lon,
                speed_mps=speed_mps,
                timestamp=timestamp,
                road_segment_id=road_segment_id,
            )

        return round(score, 4), anomalies

    def clear(self, actor_id: str | None = None) -> None:
        """Clear tracking state for one or all actors."""
        with self._lock:
            if actor_id is None:
                self._state.clear()
            else:
                self._state.pop(actor_id, None)
