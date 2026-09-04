"""Stalled vehicle detector per Playbook 8.

Identifies vehicles that are stopped in a travel lane for a prolonged
period while surrounding traffic continues to flow. The detector
explicitly suppresses stalled-vehicle classifications inside normal
congestion queues so that a traffic jam does not generate thousands of
false stalled-vehicle alerts.

Evidence requirements: prolonged low speed + lane occupancy + surrounding
traffic flow above threshold. Risk escalation occurs when the obstruction
persists while surrounding flow remains nonzero.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from packages.geo.helpers import haversine_distance
from packages.safety_policies.base import SafetyDetector
from packages.safety_policies.config import PolicyConfig
from packages.schemas.canonical import RiskEvent, RiskType, VehicleState


# Radius within which other vehicles are considered "surrounding traffic"
_SURROUNDING_RADIUS_M = 150.0


class StalledVehicleDetector(SafetyDetector):
    """Detects vehicles stalled in travel lanes, distinguishing genuine
    obstructions from normal congestion queues.

    Internal state tracks ``stopped_since`` per actor to measure how
    long a vehicle has remained below the speed threshold.
    """

    def __init__(self, config: PolicyConfig) -> None:
        self._config = config.stalled_vehicle
        self._policy_version = config.version

        # Per-actor stopped-state tracking:
        #   actor_id -> {
        #       "stopped_since": datetime,
        #       "segment_id": str | None,
        #       "lane_id": str | None,
        #       "escalated": bool,
        #   }
        self._stopped_state: dict[str, dict[str, Any]] = {}

    # -- SafetyDetector required properties ----------------------------------

    @property
    def name(self) -> str:
        return "stalled_vehicle_detector"

    @property
    def risk_type(self) -> RiskType:
        return RiskType.STALLED_VEHICLE

    @property
    def version(self) -> str:
        return self._policy_version

    # -- Core evaluation -----------------------------------------------------

    def evaluate(self, world_state: dict[str, Any]) -> list[RiskEvent]:
        """Evaluate all vehicles for stalled-in-lane conditions.

        Args:
            world_state: Must contain:
                - "vehicles": list of VehicleState dicts (or VehicleState objects)
                - "road_network" (optional): dict with "segments" list

        Returns:
            List of RiskEvent for confirmed stalled vehicles (not congestion).
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
            is_stopped = vehicle.speed_mps <= self._config.max_speed_mps

            if is_stopped:
                # Start or continue tracking stopped state
                if vehicle.actor_id not in self._stopped_state:
                    self._stopped_state[vehicle.actor_id] = {
                        "stopped_since": now,
                        "segment_id": vehicle.road_segment_id,
                        "lane_id": vehicle.lane_id,
                        "escalated": False,
                    }
                state = self._stopped_state[vehicle.actor_id]
                stopped_duration_s = (now - state["stopped_since"]).total_seconds()

                if stopped_duration_s < self._config.min_stopped_duration_s:
                    # Not stopped long enough yet
                    continue

                # -- Lane occupancy check ------------------------------------
                if self._config.lane_occupancy_required:
                    if not self._is_in_travel_lane(vehicle, segment_lookup):
                        continue

                # -- Congestion suppression ----------------------------------
                surrounding_flow = self._compute_surrounding_flow(
                    vehicle, vehicles
                )
                if surrounding_flow < self._config.surrounding_flow_threshold_mps:
                    # Surrounding traffic is also slow/stopped --
                    # this is congestion, not a stalled vehicle.
                    continue

                # -- Confirmed stalled vehicle in flowing traffic ------------
                severity = self._compute_severity(
                    vehicle, stopped_duration_s, surrounding_flow, segment_lookup
                )
                confidence = self._compute_confidence(
                    vehicle, stopped_duration_s, surrounding_flow
                )

                # Escalate if already flagged and still obstructing
                if state["escalated"]:
                    severity = min(1.0, severity * 1.2)

                state["escalated"] = True

                # Find affected actors on the same/connected segments
                affected_ids = self._find_affected_actors(
                    vehicle, vehicles, segment_lookup
                )

                risk = self.create_risk_event(
                    affected_actor_ids=[vehicle.actor_id] + affected_ids,
                    severity=severity,
                    confidence=confidence,
                    evidence=[
                        {
                            "type": "stalled_vehicle",
                            "actor_id": vehicle.actor_id,
                            "speed_mps": vehicle.speed_mps,
                            "max_speed_threshold_mps": self._config.max_speed_mps,
                            "stopped_duration_s": round(stopped_duration_s, 2),
                            "min_stopped_duration_required_s": self._config.min_stopped_duration_s,
                            "surrounding_flow_mps": round(surrounding_flow, 3)
                            if surrounding_flow != float("inf")
                            else None,
                            "surrounding_flow_threshold_mps": self._config.surrounding_flow_threshold_mps,
                            "lane_id": vehicle.lane_id,
                            "segment_id": vehicle.road_segment_id,
                            "position": {
                                "lat": vehicle.position.lat,
                                "lon": vehicle.position.lon,
                            },
                            "position_uncertainty_m": vehicle.position_uncertainty_m,
                            "escalated": state["escalated"],
                            "congestion_suppressed": False,
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
                )
                risks.append(risk)
            else:
                # Vehicle is moving -- clear stopped state
                self._stopped_state.pop(vehicle.actor_id, None)

        # Prune stale entries for actors no longer in the world state
        stale = [
            aid for aid in self._stopped_state if aid not in observed_ids
        ]
        for aid in stale:
            del self._stopped_state[aid]

        return risks

    # -- Internal helpers ----------------------------------------------------

    @staticmethod
    def _is_in_travel_lane(
        vehicle: VehicleState,
        segment_lookup: dict[str, dict[str, Any]],
    ) -> bool:
        """Determine whether the vehicle occupies a travel lane.

        A vehicle with a lane_id is considered to be in a travel lane.
        Shoulder lanes would typically have identifiers containing
        'shoulder' or 'emergency'. If lane_id is None and the vehicle has
        a valid road_segment_id, we conservatively assume it is in a
        travel lane.
        """
        if vehicle.lane_id is not None:
            lane_lower = vehicle.lane_id.lower()
            if "shoulder" in lane_lower or "emergency" in lane_lower:
                return False
            return True
        # No lane information -- accept if on a known segment
        if vehicle.road_segment_id and vehicle.road_segment_id in segment_lookup:
            return True
        return False

    @staticmethod
    def _compute_surrounding_flow(
        target: VehicleState,
        all_vehicles: list[VehicleState],
    ) -> float:
        """Compute mean speed of vehicles surrounding the target within
        ``_SURROUNDING_RADIUS_M``.

        If there are no surrounding vehicles, returns a high value to
        indicate the stopped vehicle is alone (not in congestion).
        """
        speeds: list[float] = []
        for v in all_vehicles:
            if v.actor_id == target.actor_id:
                continue
            dist = haversine_distance(
                target.position.lat,
                target.position.lon,
                v.position.lat,
                v.position.lon,
            )
            if dist <= _SURROUNDING_RADIUS_M:
                speeds.append(v.speed_mps)

        if not speeds:
            # No neighbors -- not congestion; return high flow sentinel
            return float("inf")

        return sum(speeds) / len(speeds)

    @staticmethod
    def _compute_severity(
        vehicle: VehicleState,
        stopped_duration_s: float,
        surrounding_flow: float,
        segment_lookup: dict[str, dict[str, Any]],
    ) -> float:
        """Severity increases with stopped duration and surrounding flow
        differential. Highway obstructions are more severe.
        """
        # Duration factor: longer = more severe, capped at 5 minutes
        duration_factor = min(1.0, stopped_duration_s / 300.0)

        # Flow differential: higher surrounding speed = more dangerous obstruction
        if surrounding_flow == float("inf"):
            flow_factor = 0.5
        else:
            flow_factor = min(1.0, surrounding_flow / 15.0)

        # Road type multiplier
        seg = segment_lookup.get(vehicle.road_segment_id or "")
        road_type = seg.get("type", "URBAN") if seg else "URBAN"
        type_multiplier = 1.0 if road_type == "HIGHWAY" else 0.7

        raw = (0.4 * duration_factor + 0.6 * flow_factor) * type_multiplier
        return round(min(1.0, max(0.0, raw)), 4)

    @staticmethod
    def _compute_confidence(
        vehicle: VehicleState,
        stopped_duration_s: float,
        surrounding_flow: float,
    ) -> float:
        """Confidence grows with duration and flow evidence."""
        base = 0.5

        # Duration bonus
        duration_bonus = min(0.3, stopped_duration_s / 200.0)

        # Flow evidence: clear flow differential boosts confidence
        if surrounding_flow == float("inf"):
            flow_bonus = 0.1
        else:
            flow_bonus = min(0.15, surrounding_flow * 0.01)

        # Position uncertainty penalty
        uncertainty_penalty = min(0.15, vehicle.position_uncertainty_m * 0.01)

        return round(
            min(1.0, max(0.1, base + duration_bonus + flow_bonus - uncertainty_penalty)),
            4,
        )

    @staticmethod
    def _find_affected_actors(
        stalled: VehicleState,
        all_vehicles: list[VehicleState],
        segment_lookup: dict[str, dict[str, Any]],
    ) -> list[str]:
        """Return actor IDs of vehicles on the same or connected segments
        that may be affected by the stalled vehicle.
        """
        affected: list[str] = []
        seg_id = stalled.road_segment_id
        connected: set[str] = set()
        if seg_id and seg_id in segment_lookup:
            connected = set(
                segment_lookup[seg_id].get("connected_segments", [])
            )
        relevant = ({seg_id} if seg_id else set()) | connected

        for v in all_vehicles:
            if v.actor_id == stalled.actor_id:
                continue
            if v.road_segment_id in relevant and v.speed_mps > 0:
                affected.append(v.actor_id)

        return affected


def _parse_vehicles(vehicles_raw: list[Any]) -> list[VehicleState]:
    """Accept VehicleState instances or plain dicts."""
    result: list[VehicleState] = []
    for v in vehicles_raw:
        if isinstance(v, VehicleState):
            result.append(v)
        elif isinstance(v, dict):
            result.append(VehicleState(**v))
    return result
