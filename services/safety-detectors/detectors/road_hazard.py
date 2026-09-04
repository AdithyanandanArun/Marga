"""Road hazard risk assessment detector (Playbook 9 / Section 27).

Evaluates known hazards against approaching vehicles and produces
RiskEvents when a vehicle is within warning distance and headed
toward the hazard.  Severity is scaled by the approaching vehicle's
speed so faster approaches receive higher-urgency warnings.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from packages.geo.helpers import bearing_between, bearing_difference, haversine_distance
from packages.safety_policies.base import SafetyDetector
from packages.safety_policies.config import PolicyConfig
from packages.schemas.canonical import HazardState, HazardType, RiskEvent, RiskType

_VERSION = "0.1.0"

# Vehicles with heading within this many degrees of the bearing toward a
# hazard are considered "approaching".
_APPROACH_CONE_DEG = 90.0


def _extract_point(geometry: dict[str, Any]) -> tuple[float, float] | None:
    """Return (lat, lon) from a GeoJSON geometry, or *None* if unsupported.

    Handles Point directly and uses the centroid for LineString/Polygon.
    GeoJSON coordinates are [lon, lat].
    """
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


def _hazard_expired_by_ttl(
    hazard: dict[str, Any],
    ttl_map: dict[str, int],
    now: datetime,
) -> bool:
    """Return True when the hazard has exceeded its type-specific TTL."""
    first_seen_raw = hazard.get("first_seen")
    if first_seen_raw is None:
        return False

    if isinstance(first_seen_raw, str):
        first_seen = datetime.fromisoformat(first_seen_raw)
    else:
        first_seen = first_seen_raw

    if first_seen.tzinfo is None:
        first_seen = first_seen.replace(tzinfo=timezone.utc)

    hazard_type = hazard.get("type", "OTHER")
    if isinstance(hazard_type, HazardType):
        hazard_type = hazard_type.value

    ttl_s = hazard.get("ttl_s") or ttl_map.get(hazard_type, ttl_map.get("OTHER", 3600))
    return (now - first_seen).total_seconds() > ttl_s


class RoadHazardDetector(SafetyDetector):
    """Assess risk from known road hazards to approaching vehicles.

    For every active hazard in *world_state*, the detector checks each
    vehicle and emits a ``RiskEvent`` when:

    1. The vehicle is within ``approach_warning_distance_m``.
    2. The vehicle's heading points toward the hazard (within a
       90-degree cone).

    Severity incorporates hazard severity, hazard confidence, and the
    vehicle's current speed via ``speed_severity_factor``.
    """

    def __init__(self, config: PolicyConfig) -> None:
        self._cfg = config.road_hazard

    # ------------------------------------------------------------------
    # SafetyDetector interface
    # ------------------------------------------------------------------

    @property
    def name(self) -> str:
        return "road_hazard"

    @property
    def risk_type(self) -> RiskType:
        return RiskType.ROAD_HAZARD

    @property
    def version(self) -> str:
        return _VERSION

    def evaluate(self, world_state: dict[str, Any]) -> list[RiskEvent]:
        """Evaluate hazards against vehicles and return risk events."""
        vehicles: list[dict[str, Any]] = world_state.get("vehicles", [])
        hazards: list[dict[str, Any]] = world_state.get("hazards", [])

        if not vehicles or not hazards:
            return []

        now = datetime.now(timezone.utc)
        risk_events: list[RiskEvent] = []

        for hazard in hazards:
            if self._should_skip_hazard(hazard, now):
                continue

            hazard_point = _extract_point(hazard.get("geometry", {}))
            if hazard_point is None:
                continue

            h_lat, h_lon = hazard_point
            hazard_severity: float = float(hazard.get("severity", 0.5))
            hazard_confidence: float = float(hazard.get("confidence", 0.5))

            for vehicle in vehicles:
                event = self._assess_vehicle(
                    vehicle,
                    h_lat,
                    h_lon,
                    hazard_severity,
                    hazard_confidence,
                    hazard,
                    now,
                )
                if event is not None:
                    risk_events.append(event)

        return risk_events

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _should_skip_hazard(self, hazard: dict[str, Any], now: datetime) -> bool:
        """Return True when the hazard should be excluded from evaluation."""
        state = hazard.get("state")
        if isinstance(state, HazardState):
            state = state.value
        if state == HazardState.EXPIRED.value:
            return True

        if _hazard_expired_by_ttl(hazard, self._cfg.default_ttl_s, now):
            return True

        return False

    def _assess_vehicle(
        self,
        vehicle: dict[str, Any],
        h_lat: float,
        h_lon: float,
        hazard_severity: float,
        hazard_confidence: float,
        hazard: dict[str, Any],
        now: datetime,
    ) -> RiskEvent | None:
        """Return a ``RiskEvent`` if *vehicle* is approaching the hazard."""
        position = vehicle.get("position", {})
        v_lat = position.get("lat")
        v_lon = position.get("lon")
        if v_lat is None or v_lon is None:
            return None

        distance_m = haversine_distance(v_lat, v_lon, h_lat, h_lon)
        if distance_m > self._cfg.approach_warning_distance_m:
            return None

        # -- Check the vehicle is heading toward the hazard --
        heading_deg: float = float(vehicle.get("heading_deg", 0.0))
        bearing_to_hazard = bearing_between(v_lat, v_lon, h_lat, h_lon)
        angle_off = abs(bearing_difference(heading_deg, bearing_to_hazard))
        if angle_off > _APPROACH_CONE_DEG:
            return None

        speed_mps: float = float(vehicle.get("speed_mps", 0.0))

        # Severity: base hazard severity scaled by speed and clamped to [0, 1].
        speed_component = speed_mps * self._cfg.speed_severity_factor
        raw_severity = hazard_severity * (1.0 + speed_component)
        severity = min(1.0, max(0.0, raw_severity))

        # Confidence inherits hazard confidence.
        confidence = min(1.0, max(0.0, hazard_confidence))

        # Time-to-conflict: distance / speed when moving.
        time_to_conflict_s: float | None = None
        if speed_mps > 0:
            time_to_conflict_s = distance_m / speed_mps

        actor_id = vehicle.get("actor_id", "unknown")
        hazard_id = hazard.get("hazard_id", "unknown")
        hazard_type = hazard.get("type", "OTHER")
        if isinstance(hazard_type, HazardType):
            hazard_type = hazard_type.value

        evidence: list[dict[str, Any]] = [
            {
                "hazard_id": hazard_id,
                "hazard_type": hazard_type,
                "hazard_severity": hazard_severity,
                "hazard_confidence": hazard_confidence,
                "distance_m": round(distance_m, 2),
                "approach_speed_mps": round(speed_mps, 2),
                "angle_off_deg": round(angle_off, 2),
            },
        ]

        return self.create_risk_event(
            affected_actor_ids=[actor_id],
            severity=severity,
            confidence=confidence,
            evidence=evidence,
            time_to_conflict_s=time_to_conflict_s,
            min_predicted_distance_m=distance_m,
            road_segment_id=(
                hazard.get("road_segment_id") or vehicle.get("road_segment_id")
            ),
            geometry=hazard.get("geometry"),
            ts=now,
        )
