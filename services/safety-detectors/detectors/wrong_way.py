"""Wrong-way driving detector per Playbook 8.

Detects vehicles travelling against the legal direction of a directed road
segment. Requires persistence across multiple consecutive updates to filter
transient GPS noise before emitting a RiskEvent. Broadcasts only to actors
whose route or segment is endangered by the wrong-way actor.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from packages.geo.helpers import heading_alignment
from packages.safety_policies.base import SafetyDetector
from packages.safety_policies.config import PolicyConfig
from packages.schemas.canonical import RiskEvent, RiskType, VehicleState


class WrongWayDetector(SafetyDetector):
    """Detects wrong-way driving by comparing vehicle heading against the
    legal direction of the matched road segment.

    Internal state tracks per-actor persistence counters so that only
    sustained wrong-way observations (across ``min_persistence_updates``
    consecutive ticks) produce a RiskEvent.
    """

    def __init__(self, config: PolicyConfig) -> None:
        self._config = config.wrong_way
        self._policy_version = config.version

        # Per-actor persistence tracking:
        #   actor_id -> {"count": int, "segment_id": str, "last_alignment": float}
        self._persistence: dict[str, dict[str, Any]] = {}

    # -- SafetyDetector required properties ----------------------------------

    @property
    def name(self) -> str:
        return "wrong_way_detector"

    @property
    def risk_type(self) -> RiskType:
        return RiskType.WRONG_WAY

    @property
    def version(self) -> str:
        return self._policy_version

    # -- Core evaluation -----------------------------------------------------

    def evaluate(self, world_state: dict[str, Any]) -> list[RiskEvent]:
        """Evaluate all vehicles against road-network direction.

        Args:
            world_state: Must contain:
                - "vehicles": list of VehicleState dicts (or VehicleState objects)
                - "road_network": dict with "segments" list; each segment has
                  "segment_id", "direction_deg", "connected_segments", "type"

        Returns:
            List of RiskEvent for confirmed wrong-way actors.
        """
        vehicles_raw = world_state.get("vehicles", [])
        road_network = world_state.get("road_network", {})
        segments = road_network.get("segments", [])

        if not vehicles_raw or not segments:
            return []

        segment_lookup: dict[str, dict[str, Any]] = {
            seg["segment_id"]: seg for seg in segments
        }

        vehicles = _parse_vehicles(vehicles_raw)
        now = datetime.now(timezone.utc)
        risks: list[RiskEvent] = []

        # Track which actor IDs we observe this tick so we can prune stale entries.
        observed_ids: set[str] = set()

        for vehicle in vehicles:
            observed_ids.add(vehicle.actor_id)

            # -- Gate: minimum speed (filter stationary GPS drift) -----------
            if vehicle.speed_mps < self._config.min_speed_mps:
                self._reset_persistence(vehicle.actor_id)
                continue

            # -- Map-match: find the road segment for this vehicle -----------
            matched_segment, match_confidence = self._map_match(
                vehicle, segment_lookup
            )
            if matched_segment is None:
                self._reset_persistence(vehicle.actor_id)
                continue

            if match_confidence < self._config.min_map_match_confidence:
                self._reset_persistence(vehicle.actor_id)
                continue

            # -- Heading alignment check -------------------------------------
            road_dir = matched_segment["direction_deg"]
            alignment = heading_alignment(vehicle.heading_deg, road_dir)

            if alignment < self._config.alignment_threshold:
                # Wrong-way signal -- increment persistence counter
                entry = self._persistence.get(vehicle.actor_id)
                if (
                    entry is not None
                    and entry["segment_id"] == matched_segment["segment_id"]
                ):
                    entry["count"] += 1
                    entry["last_alignment"] = alignment
                else:
                    self._persistence[vehicle.actor_id] = {
                        "count": 1,
                        "segment_id": matched_segment["segment_id"],
                        "last_alignment": alignment,
                    }

                persisted = self._persistence[vehicle.actor_id]["count"]
                if persisted >= self._config.min_persistence_updates:
                    # Confirmed wrong-way -- find endangered actors
                    endangered = self._find_endangered_actors(
                        vehicle, matched_segment, vehicles, segment_lookup
                    )
                    affected_ids = [vehicle.actor_id] + [
                        v.actor_id for v in endangered
                    ]
                    severity = self._compute_severity(
                        vehicle, matched_segment, alignment
                    )
                    confidence = min(
                        match_confidence,
                        min(1.0, persisted / (self._config.min_persistence_updates + 2)),
                    )
                    risk = self.create_risk_event(
                        affected_actor_ids=affected_ids,
                        severity=severity,
                        confidence=confidence,
                        evidence=[
                            {
                                "type": "wrong_way_detection",
                                "actor_id": vehicle.actor_id,
                                "heading_deg": vehicle.heading_deg,
                                "road_direction_deg": road_dir,
                                "alignment": round(alignment, 4),
                                "alignment_threshold": self._config.alignment_threshold,
                                "persistence_count": persisted,
                                "min_persistence_required": self._config.min_persistence_updates,
                                "map_match_confidence": round(match_confidence, 4),
                                "segment_id": matched_segment["segment_id"],
                                "segment_type": matched_segment.get("type", "UNKNOWN"),
                                "speed_mps": vehicle.speed_mps,
                                "position": {
                                    "lat": vehicle.position.lat,
                                    "lon": vehicle.position.lon,
                                },
                            }
                        ],
                        road_segment_id=matched_segment["segment_id"],
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
                # Aligned with road direction -- reset persistence
                self._reset_persistence(vehicle.actor_id)

        # Prune stale persistence entries for actors no longer present
        stale = [
            aid for aid in self._persistence if aid not in observed_ids
        ]
        for aid in stale:
            del self._persistence[aid]

        return risks

    # -- Internal helpers ----------------------------------------------------

    @staticmethod
    def _map_match(
        vehicle: VehicleState,
        segment_lookup: dict[str, dict[str, Any]],
    ) -> tuple[dict[str, Any] | None, float]:
        """Map-match a vehicle to its road segment.

        If the vehicle already carries a ``road_segment_id`` that exists in the
        network, use it directly with high confidence. Otherwise return None.

        Returns:
            (matched_segment_dict, confidence) or (None, 0.0)
        """
        seg_id = vehicle.road_segment_id
        if seg_id and seg_id in segment_lookup:
            # Direct match from upstream map-matching pipeline
            return segment_lookup[seg_id], 0.9
        return None, 0.0

    def _reset_persistence(self, actor_id: str) -> None:
        """Remove persistence tracking for an actor."""
        self._persistence.pop(actor_id, None)

    @staticmethod
    def _find_endangered_actors(
        wrong_way_vehicle: VehicleState,
        segment: dict[str, Any],
        all_vehicles: list[VehicleState],
        segment_lookup: dict[str, dict[str, Any]],
    ) -> list[VehicleState]:
        """Identify actors whose route/segment is endangered by the wrong-way
        driver.

        An actor is endangered if it is on the same segment travelling in the
        legal direction, or on a connected upstream segment (meaning the
        wrong-way driver could reach it).
        """
        endangered: list[VehicleState] = []
        seg_id = segment["segment_id"]
        connected = set(segment.get("connected_segments", []))
        # Include the current segment plus immediate connected segments
        relevant_segments = {seg_id} | connected

        for v in all_vehicles:
            if v.actor_id == wrong_way_vehicle.actor_id:
                continue
            if v.road_segment_id in relevant_segments:
                # Check that this actor is travelling in the legal direction
                # (otherwise they are also wrong-way and handled separately)
                v_seg = segment_lookup.get(v.road_segment_id or "")
                if v_seg is not None:
                    v_alignment = heading_alignment(
                        v.heading_deg, v_seg["direction_deg"]
                    )
                    if v_alignment > 0:
                        endangered.append(v)

        return endangered

    @staticmethod
    def _compute_severity(
        vehicle: VehicleState,
        segment: dict[str, Any],
        alignment: float,
    ) -> float:
        """Compute severity [0, 1] based on speed, alignment, and road type.

        Higher speed and stronger counter-alignment increase severity.
        Highway wrong-way is treated as more severe than urban.
        """
        # Speed contribution: scale by typical highway speed (~30 m/s)
        speed_factor = min(1.0, vehicle.speed_mps / 30.0)

        # Alignment contribution: -1 is worst, threshold is boundary
        alignment_factor = min(1.0, abs(alignment))

        # Road-type multiplier
        road_type = segment.get("type", "URBAN")
        type_multiplier = 1.0 if road_type == "HIGHWAY" else 0.75

        raw = (0.5 * speed_factor + 0.5 * alignment_factor) * type_multiplier
        return round(min(1.0, max(0.0, raw)), 4)


def _parse_vehicles(vehicles_raw: list[Any]) -> list[VehicleState]:
    """Accept either VehicleState instances or plain dicts."""
    result: list[VehicleState] = []
    for v in vehicles_raw:
        if isinstance(v, VehicleState):
            result.append(v)
        elif isinstance(v, dict):
            result.append(VehicleState(**v))
    return result
