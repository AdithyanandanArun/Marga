"""Emergency vehicle priority detector per Playbook 11 / Section 26.

Verifies emergency vehicle credentials, tracks active emergencies via
heartbeat, computes near-term trajectory/ETA to controlled intersections,
and produces RiskEvents with yield alerts for affected corridor users plus
SignalPriorityRequest objects for intersection signal pre-emption.

Key design choices
------------------
* Credential verification is mandatory when ``credential_required`` is
  set.  Setting ``actor_type=AMBULANCE`` alone never grants privileges.
* Heartbeat-based lifecycle: emergency status expires after
  ``heartbeat_timeout_s`` without a fresh vehicle state update.
* Yield alerts target only vehicles in the same/relevant corridor when
  ``corridor_relevance_only`` is enabled.
* Every verification, acceptance, and rejection is logged for audit.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from packages.geo.helpers import bearing_difference, haversine_distance
from packages.safety_policies.base import SafetyDetector
from packages.safety_policies.config import PolicyConfig
from packages.schemas.canonical import (
    RiskEvent,
    RiskType,
    SignalPriorityRequest,
)

logger = logging.getLogger(__name__)

_DETECTOR_VERSION = "0.1.0"

# Maximum heading deviation to consider a vehicle "in the same corridor".
_CORRIDOR_HEADING_TOLERANCE_DEG = 45.0


class EmergencyVehicleDetector(SafetyDetector):
    """Detect and manage emergency vehicle priority situations."""

    def __init__(self, config: PolicyConfig) -> None:
        self._cfg = config.emergency_vehicle
        # Tracks verified emergency vehicles.
        # Key: actor_id, Value: dict with credential info and timestamps.
        self._active_emergencies: dict[str, dict[str, Any]] = {}

    # -- SafetyDetector protocol -----------------------------------------

    @property
    def name(self) -> str:
        return "emergency_vehicle"

    @property
    def risk_type(self) -> RiskType:
        return RiskType.EMERGENCY_VEHICLE

    @property
    def version(self) -> str:
        return _DETECTOR_VERSION

    # -- public API ------------------------------------------------------

    def evaluate(self, world_state: dict[str, Any]) -> list[RiskEvent]:
        now = datetime.now(timezone.utc)

        vehicles = world_state.get("vehicles", [])
        credentials = world_state.get("emergency_credentials", {})
        intersections = world_state.get("intersections", [])
        road_network = world_state.get("road_network", {})

        vehicles_by_id = {v.get("actor_id", ""): v for v in vehicles if v.get("actor_id")}

        # Phase 1 -- credential verification and heartbeat management.
        self._process_credentials(credentials, vehicles_by_id, now)
        self._expire_stale_emergencies(now)

        if not self._active_emergencies:
            return []

        risk_events: list[RiskEvent] = []
        segments_by_id = _index_segments(road_network.get("segments", []))

        for actor_id, em_state in self._active_emergencies.items():
            ev = vehicles_by_id.get(actor_id)
            if ev is None:
                continue

            ev_pos = ev.get("position", {})
            ev_lat = ev_pos.get("lat")
            ev_lon = ev_pos.get("lon")
            ev_heading = ev.get("heading_deg", 0.0)
            ev_speed = ev.get("speed_mps", 0.0)
            ev_segment = ev.get("road_segment_id")

            if ev_lat is None or ev_lon is None:
                continue

            # Phase 2 -- identify affected road users and produce yield risks.
            affected_actors = self._find_affected_actors(
                ev, vehicles, ev_lat, ev_lon, ev_heading, ev_segment, segments_by_id,
            )

            if affected_actors:
                affected_ids = [a.get("actor_id", "unknown") for a in affected_actors]
                yield_evidence = {
                    "type": "emergency_yield",
                    "emergency_actor_id": actor_id,
                    "emergency_type": em_state.get("emergency_type", "UNKNOWN"),
                    "credential_ref": em_state.get("credential_ref", ""),
                    "emergency_speed_mps": round(ev_speed, 2),
                    "emergency_heading_deg": round(ev_heading, 1),
                    "affected_actor_count": len(affected_ids),
                    "corridor_relevance_only": self._cfg.corridor_relevance_only,
                }

                severity = _yield_severity(ev_speed, len(affected_actors))
                risk_events.append(
                    self.create_risk_event(
                        affected_actor_ids=[actor_id, *affected_ids],
                        severity=severity,
                        confidence=em_state.get("confidence", 0.9),
                        evidence=[yield_evidence],
                        road_segment_id=ev_segment,
                    )
                )

            # Phase 3 -- signal priority requests for nearby intersections.
            priority_requests = self._generate_signal_priority(
                ev, em_state, intersections, now,
            )
            for spr in priority_requests:
                request_evidence = {
                    "type": "signal_priority_request",
                    "emergency_actor_id": actor_id,
                    "intersection_id": spr.intersection_id,
                    "desired_movement": spr.desired_movement,
                    "eta_window_s": list(spr.eta_window_s),
                    "credential_ref": spr.credential_ref,
                    "request_id": spr.request_id,
                }

                risk_events.append(
                    self.create_risk_event(
                        affected_actor_ids=[actor_id],
                        severity=0.7,
                        confidence=em_state.get("confidence", 0.9),
                        evidence=[request_evidence],
                        time_to_conflict_s=spr.eta_window_s[0],
                    )
                )

        return risk_events

    # -- credential verification -----------------------------------------

    def _process_credentials(
        self,
        credentials: dict[str, dict[str, Any]],
        vehicles_by_id: dict[str, dict[str, Any]],
        now: datetime,
    ) -> None:
        """Verify and register emergency vehicles from credential data."""
        for actor_id, cred in credentials.items():
            verified = cred.get("verified", False)
            credential_ref = cred.get("credential_ref", "")
            emergency_type = cred.get("emergency_type", "UNKNOWN")
            expires_at_raw = cred.get("expires_at")

            # Check expiry.
            if expires_at_raw:
                if isinstance(expires_at_raw, str):
                    try:
                        expires_at = datetime.fromisoformat(expires_at_raw)
                    except ValueError:
                        logger.warning(
                            "Invalid expires_at for actor %s: %s", actor_id, expires_at_raw,
                        )
                        expires_at = None
                else:
                    expires_at = expires_at_raw
                if expires_at is not None and expires_at < now:
                    logger.info(
                        "AUDIT: Credential expired for actor %s, credential_ref=%s",
                        actor_id, credential_ref,
                    )
                    self._active_emergencies.pop(actor_id, None)
                    continue

            # Credential verification gate.
            if self._cfg.credential_required and not verified:
                logger.info(
                    "AUDIT: Rejected unverified emergency request from actor %s, "
                    "credential_ref=%s",
                    actor_id, credential_ref,
                )
                self._active_emergencies.pop(actor_id, None)
                continue

            # Actor must actually be present in the vehicle feed.
            if actor_id not in vehicles_by_id:
                logger.info(
                    "AUDIT: Emergency actor %s has credential but no vehicle state",
                    actor_id,
                )
                continue

            # Accept / refresh.
            if actor_id not in self._active_emergencies:
                logger.info(
                    "AUDIT: Accepted emergency status for actor %s, type=%s, "
                    "credential_ref=%s",
                    actor_id, emergency_type, credential_ref,
                )
            else:
                logger.debug(
                    "AUDIT: Heartbeat refresh for emergency actor %s", actor_id,
                )

            self._active_emergencies[actor_id] = {
                "credential_ref": credential_ref,
                "emergency_type": emergency_type,
                "last_heartbeat": now,
                "confidence": 0.95 if verified else 0.6,
            }

    def _expire_stale_emergencies(self, now: datetime) -> None:
        """Remove emergencies that have not received a heartbeat."""
        timeout = timedelta(seconds=self._cfg.heartbeat_timeout_s)
        expired = [
            aid for aid, state in self._active_emergencies.items()
            if now - state.get("last_heartbeat", now) > timeout
        ]
        for aid in expired:
            logger.info(
                "AUDIT: Emergency status expired (heartbeat timeout) for actor %s",
                aid,
            )
            del self._active_emergencies[aid]

    # -- corridor / affected actors --------------------------------------

    def _find_affected_actors(
        self,
        emergency_vehicle: dict[str, Any],
        all_vehicles: list[dict[str, Any]],
        ev_lat: float,
        ev_lon: float,
        ev_heading: float,
        ev_segment: str | None,
        segments_by_id: dict[str, dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Identify road users that need to yield."""
        ev_id = emergency_vehicle.get("actor_id")
        affected: list[dict[str, Any]] = []

        for v in all_vehicles:
            v_id = v.get("actor_id")
            if v_id == ev_id or v_id in self._active_emergencies:
                continue

            v_pos = v.get("position", {})
            v_lat = v_pos.get("lat")
            v_lon = v_pos.get("lon")
            if v_lat is None or v_lon is None:
                continue

            dist = haversine_distance(ev_lat, ev_lon, v_lat, v_lon)
            if dist > self._cfg.yield_alert_distance_m:
                continue

            if self._cfg.corridor_relevance_only:
                if not _is_corridor_relevant(
                    ev_heading, ev_segment,
                    v.get("heading_deg", 0.0), v.get("road_segment_id"),
                    segments_by_id,
                ):
                    continue

            affected.append(v)

        return affected

    # -- signal priority -------------------------------------------------

    def _generate_signal_priority(
        self,
        ev: dict[str, Any],
        em_state: dict[str, Any],
        intersections: list[dict[str, Any]],
        now: datetime,
    ) -> list[SignalPriorityRequest]:
        """Build SignalPriorityRequests for intersections ahead of the EV."""
        ev_pos = ev.get("position", {})
        ev_lat = ev_pos.get("lat")
        ev_lon = ev_pos.get("lon")
        ev_speed = ev.get("speed_mps", 0.0)
        ev_segment = ev.get("road_segment_id", "")
        if ev_lat is None or ev_lon is None or ev_speed <= 0:
            return []

        requests: list[SignalPriorityRequest] = []

        for intersection in intersections:
            int_pos = intersection.get("position", {})
            i_lat = int_pos.get("lat")
            i_lon = int_pos.get("lon")
            if i_lat is None or i_lon is None:
                continue

            dist = haversine_distance(ev_lat, ev_lon, i_lat, i_lon)
            if dist > self._cfg.yield_alert_distance_m:
                continue

            eta_mean = dist / ev_speed
            eta_lo = max(0.0, eta_mean - 2.0)
            eta_hi = eta_mean + 3.0

            int_id = intersection.get("intersection_id", "unknown")
            credential_ref = em_state.get("credential_ref", "")

            spr = SignalPriorityRequest(
                intersection_id=int_id,
                desired_movement=ev_segment,
                eta_window_s=(eta_lo, eta_hi),
                requester_id=ev.get("actor_id", "unknown"),
                credential_ref=credential_ref,
                emergency_type=em_state.get("emergency_type", "UNKNOWN"),
                expires_at=now + timedelta(seconds=eta_hi + 10),
                ts=now,
            )
            requests.append(spr)

            logger.info(
                "AUDIT: Signal priority request created for intersection %s, "
                "requester=%s, eta_window=(%.1f, %.1f)s",
                int_id, ev.get("actor_id"), eta_lo, eta_hi,
            )

        return requests


# -- module-level helpers ------------------------------------------------


def _index_segments(segments: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {s["segment_id"]: s for s in segments if "segment_id" in s}


def _is_corridor_relevant(
    ev_heading: float,
    ev_segment: str | None,
    other_heading: float,
    other_segment: str | None,
    segments_by_id: dict[str, dict[str, Any]],
) -> bool:
    """Determine if another vehicle is in the same/relevant corridor.

    Two vehicles are corridor-relevant if they share a segment, share a
    connected segment, or their headings are roughly aligned or opposed
    (facing the emergency vehicle).
    """
    # Same segment is always relevant.
    if ev_segment and other_segment and ev_segment == other_segment:
        return True

    # Connected segments are relevant.
    if ev_segment and other_segment:
        ev_seg_meta = segments_by_id.get(ev_segment, {})
        connected = ev_seg_meta.get("connected_segments", [])
        if other_segment in connected:
            return True

    # Heading-based corridor heuristic: aligned or opposing.
    diff = abs(bearing_difference(ev_heading, other_heading))
    if diff <= _CORRIDOR_HEADING_TOLERANCE_DEG or diff >= (180 - _CORRIDOR_HEADING_TOLERANCE_DEG):
        return True

    return False


def _yield_severity(ev_speed: float, affected_count: int) -> float:
    """Severity for yield risk: faster EV and more affected actors = higher."""
    speed_factor = min(1.0, ev_speed / 25.0)
    count_factor = min(1.0, affected_count / 10.0)
    return min(1.0, 0.4 + 0.3 * speed_factor + 0.3 * count_factor)
