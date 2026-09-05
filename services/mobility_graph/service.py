"""Bounded in-memory projection of canonical state onto road-graph edges."""

from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from datetime import UTC, datetime, timedelta
from statistics import fmean
from typing import Any

from packages.schemas.canonical import ActorType, Hazard, PedestrianState, TrafficSignalState, VehicleState
from packages.schemas.mobility_graph import (
    GraphEdgeDefinition,
    MobilityEdgeState,
    MobilityIntersectionState,
    RollingEdgeMetrics,
)

WINDOWS_S = (5, 15, 30, 60)
QUEUE_SPEED_MPS = 2.0


def _utc(ts: datetime) -> datetime:
    return ts if ts.tzinfo else ts.replace(tzinfo=UTC)


class MobilityGraphService:
    """Projects canonical actor state into confidence-aware edge metrics.

    Topology is registered by adapters; this service never imports SUMO or
    OSM-specific types. Memory is bounded to the maximum required history.
    """

    def __init__(self) -> None:
        self._definitions: dict[str, GraphEdgeDefinition] = {}
        self._vehicles: dict[str, VehicleState] = {}
        self._pedestrians: dict[str, PedestrianState] = {}
        self._actor_edges: dict[str, str] = {}
        self._entry_times: dict[str, deque[tuple[datetime, str]]] = defaultdict(deque)
        self._samples: dict[str, deque[tuple[datetime, int, float, int, float]]] = defaultdict(deque)
        self._hazards: dict[str, list[Hazard]] = defaultdict(list)
        self._signals: dict[str, TrafficSignalState] = {}
        self._subscribers: list[asyncio.Queue[dict[str, Any]]] = []

    def register_edge(self, definition: GraphEdgeDefinition) -> MobilityEdgeState:
        self._definitions[definition.edge_id] = definition
        return self._record_and_publish_edge(definition.edge_id, datetime.now(UTC))

    def observe_vehicle(self, state: VehicleState) -> list[MobilityEdgeState]:
        return self._observe_actor(state, self._vehicles)

    def observe_pedestrian(self, state: PedestrianState) -> list[MobilityEdgeState]:
        return self._observe_actor(state, self._pedestrians)

    def observe_signal(self, state: TrafficSignalState) -> MobilityIntersectionState | None:
        self._signals[state.signal_id] = state
        return self._publish_intersection(state.intersection_id, state.ts)

    def observe_hazard(self, hazard: Hazard) -> MobilityEdgeState | None:
        if not hazard.road_segment_id:
            return None
        self._hazards[hazard.road_segment_id].append(hazard)
        return self._record_and_publish_edge(hazard.road_segment_id, hazard.last_seen)

    def update_position_quality(
        self, actor_id: str, uncertainty_m: float, ts: datetime
    ) -> MobilityEdgeState | None:
        """Apply a canonical positioning-quality update to the graph projection."""
        vehicle = self._vehicles.get(actor_id)
        pedestrian = self._pedestrians.get(actor_id)
        actor = vehicle or pedestrian
        if actor is None or actor.road_segment_id is None:
            return None
        if vehicle is not None:
            updated_vehicle = vehicle.model_copy(
                update={"position_uncertainty_m": uncertainty_m, "ts": _utc(ts)}
            )
            self._vehicles[actor_id] = updated_vehicle
        else:
            assert pedestrian is not None
            updated_pedestrian = pedestrian.model_copy(
                update={"position_uncertainty_m": uncertainty_m, "ts": _utc(ts)}
            )
            self._pedestrians[actor_id] = updated_pedestrian
        return self._record_and_publish_edge(actor.road_segment_id, ts)

    def get_edge(self, edge_id: str) -> MobilityEdgeState | None:
        if edge_id not in self._known_edges():
            return None
        return self._edge_state(edge_id, datetime.now(UTC), record=False)

    def get_intersection(self, intersection_id: str) -> MobilityIntersectionState | None:
        if not any(item.intersection_id == intersection_id for item in self._definitions.values()):
            return None
        return self._intersection_state(intersection_id, datetime.now(UTC))

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=32)
        self._subscribers.append(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        if queue in self._subscribers:
            self._subscribers.remove(queue)

    def _observe_actor(self, state: VehicleState | PedestrianState, store: dict[str, Any]) -> list[MobilityEdgeState]:
        actor_id = state.actor_id
        prior_edge = self._actor_edges.get(actor_id)
        edge_id = state.road_segment_id
        store[actor_id] = state
        if edge_id is None:
            self._actor_edges.pop(actor_id, None)
            return [self._record_and_publish_edge(prior_edge, state.ts)] if prior_edge else []
        if edge_id not in self._definitions:
            self._definitions[edge_id] = GraphEdgeDefinition(edge_id=edge_id, source="actor-observation")
        self._actor_edges[actor_id] = edge_id
        if prior_edge != edge_id:
            self._entry_times[edge_id].append((_utc(state.ts), actor_id))
        affected = {edge_id}
        if prior_edge and prior_edge != edge_id:
            affected.add(prior_edge)
        return [self._record_and_publish_edge(item, state.ts) for item in sorted(affected)]

    def _known_edges(self) -> set[str]:
        return set(self._definitions) | set(self._actor_edges.values())

    def _actors_on(self, edge_id: str) -> tuple[list[VehicleState], list[PedestrianState]]:
        vehicles = [item for item in self._vehicles.values() if item.road_segment_id == edge_id]
        pedestrians = [item for item in self._pedestrians.values() if item.road_segment_id == edge_id]
        return vehicles, pedestrians

    def _edge_state(self, edge_id: str, ts: datetime, *, record: bool) -> MobilityEdgeState:
        now = _utc(ts)
        definition = self._definitions[edge_id]
        vehicles, pedestrians = self._actors_on(edge_id)
        vehicle_count = len(vehicles)
        capacity_ratio = vehicle_count / definition.capacity_vehicles
        occupancy = min(1.0, capacity_ratio)
        avg_speed = fmean(item.speed_mps for item in vehicles) if vehicles else 0.0
        queue_length = sum(item.speed_mps <= QUEUE_SPEED_MPS for item in vehicles)
        two_wheelers = sum(item.actor_type in {ActorType.BIKE, ActorType.CYCLIST} for item in vehicles)
        observed: list[VehicleState | PedestrianState] = [*vehicles, *pedestrians]
        uncertainty = fmean(item.position_uncertainty_m for item in observed) if observed else 0.0
        gps_confidence = 1.0 / (1.0 + uncertainty / 10.0)
        self._trim(edge_id, now)
        flow_rate = len(self._entry_times[edge_id]) * 3600.0 / 60.0
        active_hazards = [
            item
            for item in self._hazards[edge_id]
            if _utc(item.last_seen) + timedelta(seconds=item.ttl_s) >= now
        ]
        self._hazards[edge_id] = active_hazards
        hazard_penalty = max((item.severity * item.confidence for item in active_hazards), default=0.0)
        downstream = fmean(
            len(self._actors_on(next_edge)[0]) / self._definitions[next_edge].capacity_vehicles
            for next_edge in definition.downstream_edge_ids
            if next_edge in self._definitions
        ) if definition.downstream_edge_ids else 0.0
        if record:
            self._samples[edge_id].append((now, vehicle_count, avg_speed, queue_length, occupancy))
            self._trim(edge_id, now)
        windows = self._rolling(edge_id, now)
        source_factor = 0.65 if definition.source == "actor-observation" else 1.0
        confidence = min(1.0, (0.4 + 0.6 * gps_confidence) * source_factor)
        return MobilityEdgeState(
            edge_id=edge_id,
            ts=now,
            intersection_id=definition.intersection_id,
            lane_count=definition.lane_count,
            capacity_vehicles=definition.capacity_vehicles,
            vehicle_count=vehicle_count,
            pedestrian_count=len(pedestrians),
            density=capacity_ratio,
            two_wheeler_ratio=two_wheelers / vehicle_count if vehicle_count else 0.0,
            avg_speed_mps=avg_speed,
            queue_length=queue_length,
            flow_rate_vph=flow_rate,
            occupancy=occupancy,
            capacity_ratio=capacity_ratio,
            hazard_penalty=hazard_penalty,
            gps_confidence=gps_confidence,
            downstream_congestion=downstream,
            rolling_windows=windows,
            confidence=confidence,
            evidence=[
                {"type": "actor_count", "value": vehicle_count},
                {"type": "gps_uncertainty_m", "value": uncertainty},
            ],
            provenance=[definition.source, "canonical-actor-state"],
        )

    def _record_and_publish_edge(self, edge_id: str, ts: datetime) -> MobilityEdgeState:
        state = self._edge_state(edge_id, ts, record=True)
        self._publish("graph.edge.updated", state.model_dump(mode="json"))
        if state.intersection_id:
            self._publish_intersection(state.intersection_id, state.ts)
        return state

    def _publish_intersection(self, intersection_id: str, ts: datetime) -> MobilityIntersectionState | None:
        state = self._intersection_state(intersection_id, ts)
        if state is not None:
            self._publish("graph.intersection.updated", state.model_dump(mode="json"))
        return state

    def _intersection_state(self, intersection_id: str, ts: datetime) -> MobilityIntersectionState | None:
        edge_ids = sorted(
            item.edge_id for item in self._definitions.values() if item.intersection_id == intersection_id
        )
        if not edge_ids:
            return None
        states = [self._edge_state(edge_id, ts, record=False) for edge_id in edge_ids]
        total_vehicles = sum(item.vehicle_count for item in states)
        total_pedestrians = sum(item.pedestrian_count for item in states)
        return MobilityIntersectionState(
            intersection_id=intersection_id,
            ts=_utc(ts),
            edge_ids=edge_ids,
            vehicle_count=total_vehicles,
            pedestrian_count=total_pedestrians,
            avg_speed_mps=fmean(item.avg_speed_mps for item in states),
            queue_length=sum(item.queue_length for item in states),
            occupancy=fmean(item.occupancy for item in states),
            downstream_congestion=fmean(item.downstream_congestion for item in states),
            gps_confidence=fmean(item.gps_confidence for item in states),
            confidence=fmean(item.confidence for item in states),
            evidence=[{"type": "edge_aggregate", "edge_count": len(edge_ids)}],
            provenance=["live-mobility-graph"],
        )

    def _trim(self, edge_id: str, now: datetime) -> None:
        cutoff = now - timedelta(seconds=max(WINDOWS_S))
        for values in (self._entry_times[edge_id], self._samples[edge_id]):
            while values and values[0][0] < cutoff:
                values.popleft()

    def _rolling(self, edge_id: str, now: datetime) -> dict[str, RollingEdgeMetrics]:
        result: dict[str, RollingEdgeMetrics] = {}
        samples = self._samples[edge_id]
        for window in WINDOWS_S:
            subset = [item for item in samples if item[0] >= now - timedelta(seconds=window)]
            entries = [item for item in self._entry_times[edge_id] if item[0] >= now - timedelta(seconds=window)]
            count = len(subset)
            result[str(window)] = RollingEdgeMetrics(
                window_s=window, sample_count=count,
                avg_vehicle_count=fmean(item[1] for item in subset) if subset else 0.0,
                avg_speed_mps=fmean(item[2] for item in subset) if subset else 0.0,
                avg_queue_length=fmean(item[3] for item in subset) if subset else 0.0,
                avg_occupancy=fmean(item[4] for item in subset) if subset else 0.0,
                flow_rate_vph=len(entries) * 3600.0 / window,
            )
        return result

    def _publish(self, event_type: str, data: dict[str, Any]) -> None:
        message = {
            "event_type": event_type,
            "schema_version": "0.1.0",
            "ts": datetime.now(UTC).isoformat(),
            "data": data,
        }
        for queue in tuple(self._subscribers):
            try:
                queue.put_nowait(message)
            except asyncio.QueueFull:
                pass


mobility_graph = MobilityGraphService()
