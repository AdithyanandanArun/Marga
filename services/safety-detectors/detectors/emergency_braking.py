"""Emergency braking detector per Playbook 8.

Detects vehicles undergoing hard deceleration (explicit brake events or
acceleration below the configured threshold) for a sustained duration.
Attaches event timestamp, position uncertainty, actor direction, and a
short TTL. Targets following and merging actors by road topology and
closing time.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from packages.geo.helpers import bearing_between, bearing_difference, haversine_distance
from packages.safety_policies.base import SafetyDetector
from packages.safety_policies.config import PolicyConfig
from packages.schemas.canonical import RiskEvent, RiskType, VehicleState


class EmergencyBrakingDetector(SafetyDetector):
    """Detects hard-braking events and warns following/merging actors.

    Internal state tracks braking duration per actor so that short
    jitter in acceleration data does not produce false positives.
    """

    def __init__(self, config: PolicyConfig) -> None:
        self._config = config.emergency_braking
        self._policy_version = config.version

        # Per-actor braking state:
        #   actor_id -> {
        #       "braking_since": datetime,
        #       "last_ts": datetime,
        #       "peak_decel_mps2": float,
        #       "segment_id": str | None,
        #   }
        self._braking_state: dict[str, dict[str, Any]] = {}

    # -- SafetyDetector required properties ----------------------------------

    @property
    def name(self) -> str:
        return "emergency_braking_detector"

    @property
    def risk_type(self) -> RiskType:
        return RiskType.EMERGENCY_BRAKING

    @property
    def version(self) -> str:
        return self._policy_version

    # -- Core evaluation -----------------------------------------------------

    def evaluate(self, world_state: dict[str, Any]) -> list[RiskEvent]:
        """Evaluate all vehicles for hard-braking conditions.

        Args:
            world_state: Must contain:
                - "vehicles": list of VehicleState dicts (or VehicleState objects)
                - "road_network" (optional): dict with "segments" list

        Returns:
            List of RiskEvent for confirmed emergency braking actors.
        """
        vehicles_raw = world_state.get("vehicles", [])
        road_network = world_state.get("road_network", {})
        segments = road_network.get("segments", [])

        if not vehicles_raw:
            return []

        segment_lookup: dict[str, dict[str, Any]] = {
            seg["segment_id"]: seg for seg in segments
        }

        vehicles = _parse_vehicles(vehicles_raw)
        now = datetime.now(timezone.utc)
        risks: list[RiskEvent] = []
        observed_ids: set[str] = set()

        for vehicle in vehicles:
            observed_ids.add(vehicle.actor_id)
            is_braking = self._is_hard_braking(vehicle)

            if is_braking:
                state = self._braking_state.get(vehicle.actor_id)
                if state is not None:
                    # Already tracking this actor -- update
                    state["last_ts"] = now
                    if vehicle.acceleration_mps2 is not None:
                        state["peak_decel_mps2"] = min(
                            state["peak_decel_mps2"],
                            vehicle.acceleration_mps2,
                        )
                else:
                    # Start tracking
                    self._braking_state[vehicle.actor_id] = {
                        "braking_since": now,
                        "last_ts": now,
                        "peak_decel_mps2": (
                            vehicle.acceleration_mps2
                            if vehicle.acceleration_mps2 is not None
                            else self._config.deceleration_threshold_mps2
                        ),
                        "segment_id": vehicle.road_segment_id,
                    }

                state = self._braking_state[vehicle.actor_id]
                braking_duration_s = (now - state["braking_since"]).total_seconds()

                if braking_duration_s >= self._config.min_duration_s:
                    # Confirmed emergency braking -- find target actors
                    targets = self._find_target_actors(
                        vehicle, vehicles, segment_lookup
                    )
                    affected_ids = [vehicle.actor_id] + [
                        t.actor_id for t in targets
                    ]
                    severity = self._compute_severity(vehicle, state)
                    confidence = self._compute_confidence(
                        vehicle, braking_duration_s
                    )
                    expires_at = now + timedelta(
                        seconds=self._config.alert_ttl_s
                    )
                    risk = self.create_risk_event(
                        affected_actor_ids=affected_ids,
                        severity=severity,
                        confidence=confidence,
                        evidence=[
                            {
                                "type": "emergency_braking",
                                "actor_id": vehicle.actor_id,
                                "acceleration_mps2": vehicle.acceleration_mps2,
                                "peak_deceleration_mps2": state["peak_decel_mps2"],
                                "deceleration_threshold_mps2": self._config.deceleration_threshold_mps2,
                                "braking_duration_s": round(braking_duration_s, 3),
                                "min_duration_required_s": self._config.min_duration_s,
                                "speed_mps": vehicle.speed_mps,
                                "heading_deg": vehicle.heading_deg,
                                "position": {
                                    "lat": vehicle.position.lat,
                                    "lon": vehicle.position.lon,
                                },
                                "position_uncertainty_m": vehicle.position_uncertainty_m,
                                "segment_id": vehicle.road_segment_id,
                                "target_actor_ids": [t.actor_id for t in targets],
                                "event_timestamp": now.isoformat(),
                            }
                        ],
                        road_segment_id=vehicle.road_segment_id,
                        geometry={
                            "type": "Point",
                            "coordinates": [
                                vehicle.position.lon,
                                vehicle.position.lat,
                            ],
                        },
                        ts=now,
                        expires_at=expires_at,
                    )
                    risks.append(risk)
            else:
                # Not braking -- clear state
                self._braking_state.pop(vehicle.actor_id, None)

        # Prune stale entries for actors no longer in the world state
        stale = [
            aid for aid in self._braking_state if aid not in observed_ids
        ]
        for aid in stale:
            del self._braking_state[aid]

        return risks

    # -- Internal helpers ----------------------------------------------------

    def _is_hard_braking(self, vehicle: VehicleState) -> bool:
        """Determine whether the vehicle is undergoing hard braking.

        Uses explicit acceleration data when available. The check fires
        when acceleration is at or below the configured deceleration
        threshold (a negative value).
        """
        if vehicle.acceleration_mps2 is None:
            return False
        return vehicle.acceleration_mps2 <= self._config.deceleration_threshold_mps2

    def _find_target_actors(
        self,
        braking_vehicle: VehicleState,
        all_vehicles: list[VehicleState],
        segment_lookup: dict[str, dict[str, Any]],
    ) -> list[VehicleState]:
        """Find following/merging actors that should be warned.

        Targets actors on the same or connected segments that are closing
        on the braking vehicle within the configured time threshold.
        """
        targets: list[VehicleState] = []
        seg_id = braking_vehicle.road_segment_id
        connected: set[str] = set()
        if seg_id and seg_id in segment_lookup:
            connected = set(
                segment_lookup[seg_id].get("connected_segments", [])
            )
        relevant_segments = ({seg_id} if seg_id else set()) | connected

        for v in all_vehicles:
            if v.actor_id == braking_vehicle.actor_id:
                continue

            # Check topological relevance
            if v.road_segment_id not in relevant_segments:
                continue

            # Compute closing time -- only warn actors that are behind
            # the braking vehicle and closing in.
            distance_m = haversine_distance(
                v.position.lat,
                v.position.lon,
                braking_vehicle.position.lat,
                braking_vehicle.position.lon,
            )
            # Bearing from the following vehicle to the braking vehicle
            bearing_to_braker = bearing_difference(
                v.heading_deg,
                bearing_between(
                    v.position.lat,
                    v.position.lon,
                    braking_vehicle.position.lat,
                    braking_vehicle.position.lon,
                ),
            )
            # The braking vehicle should be roughly ahead (bearing diff < 90)
            if abs(bearing_to_braker) > 90:
                continue

            # Closing speed: follower speed minus braker speed
            closing_speed = v.speed_mps - braking_vehicle.speed_mps
            if closing_speed <= 0:
                continue

            closing_time = distance_m / closing_speed
            if closing_time <= self._config.closing_time_threshold_s:
                targets.append(v)

        return targets

    def _compute_severity(
        self, vehicle: VehicleState, state: dict[str, Any]
    ) -> float:
        """Severity based on deceleration magnitude and speed."""
        peak = abs(state["peak_decel_mps2"])
        threshold = abs(self._config.deceleration_threshold_mps2)

        # Deceleration factor: how much worse than threshold
        decel_factor = min(1.0, peak / (threshold * 2))

        # Speed factor: faster braking is more dangerous
        speed_factor = min(1.0, vehicle.speed_mps / 30.0)

        raw = 0.6 * decel_factor + 0.4 * speed_factor
        return round(min(1.0, max(0.0, raw)), 4)

    def _compute_confidence(
        self, vehicle: VehicleState, duration_s: float
    ) -> float:
        """Confidence increases with braking duration and data quality."""
        # Base confidence from having acceleration data
        base = 0.7

        # Duration bonus: longer braking is more certain
        duration_bonus = min(0.2, duration_s * 0.05)

        # Position uncertainty penalty
        uncertainty_penalty = min(0.2, vehicle.position_uncertainty_m * 0.01)

        return round(
            min(1.0, max(0.1, base + duration_bonus - uncertainty_penalty)), 4
        )


def _parse_vehicles(vehicles_raw: list[Any]) -> list[VehicleState]:
    """Accept VehicleState instances or plain dicts."""
    result: list[VehicleState] = []
    for v in vehicles_raw:
        if isinstance(v, VehicleState):
            result.append(v)
        elif isinstance(v, dict):
            result.append(VehicleState(**v))
    return result
