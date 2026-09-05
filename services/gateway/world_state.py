"""Canonical live world state, ingestion endpoints, and WebSocket deltas."""

from __future__ import annotations

import asyncio
import logging
import math
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from packages.schemas.canonical import (
    ConnectivityEvent,
    Hazard,
    HazardState,
    HazardType,
    PedestrianState,
    PositionQualityEvent,
    VehicleState,
)
from packages.schemas.hazards import HazardObservation
from services.integration.canonical_bridge import vehicle_from_adapter_event
from services.position import PositionFusionService, predict_trajectory
from services.risk import RiskEngine
from services.policy_learning import ContextualSafetyBandit, PolicyContext

from .incidents import incident_traces

logger = logging.getLogger("marga.gateway.world_state")
router = APIRouter(tags=["world-state"])

EntityType = Literal["vehicle", "pedestrian", "hazard", "risk"]
_entities: dict[tuple[EntityType, str], dict[str, Any]] = {}
_subscribers: list[asyncio.Queue[dict[str, Any]]] = []
_position_fusion = PositionFusionService()
_risk_engine = RiskEngine()
_policy_learner = ContextualSafetyBandit()

# Latest connectivity state and per-actor position quality (enriched in every WS delta)
_connectivity_state: dict[str, Any] | None = None
_position_quality: dict[str, dict[str, Any]] = {}  # actor_id → PositionQualityEvent dict


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _entity(entity_type: EntityType, entity_id: str, data: dict[str, Any]) -> dict[str, Any]:
    return {"entity_type": entity_type, "entity_id": entity_id, "data": data}


def _entities_as_list(entity_type: EntityType | None = None) -> list[dict[str, Any]]:
    return [
        _entity(kind, entity_id, data)
        for (kind, entity_id), data in _entities.items()
        if entity_type is None or kind == entity_type
    ]


def _world_delta(
    kind: Literal["snapshot", "delta"],
    upserts: list[dict[str, Any]],
    deletes: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"kind": kind, "server_time": _now(), "upserts": upserts, "deletes": deletes or []}
    # Existing dashboard revisions expect this legacy field. New clients use
    # the canonical upserts/deletes contract above.
    payload["actors"] = [item["data"] for item in _entities_as_list("vehicle")]
    # Connectivity and position-quality are broadcast in every delta so the
    # dashboard can always show the current resilience state.
    payload["connectivity"] = _connectivity_state
    payload["position_quality"] = dict(_position_quality)
    return payload


def _snapshot_delta() -> dict[str, Any]:
    return _world_delta("snapshot", _entities_as_list())


def _legacy_snapshot() -> dict[str, Any]:
    return {
        "actors": [item["data"] for item in _entities_as_list("vehicle")],
        "pedestrians": [item["data"] for item in _entities_as_list("pedestrian")],
        "hazards": [item["data"] for item in _entities_as_list("hazard")],
        "risks": [item["data"] for item in _entities_as_list("risk")],
    }


def _notify(delta: dict[str, Any]) -> None:
    for queue in tuple(_subscribers):
        try:
            queue.put_nowait(delta)
        except asyncio.QueueFull:
            logger.warning("dropping world delta for slow WebSocket client")


def _store_model(entity_type: EntityType, entity_id: str, model: BaseModel) -> dict[str, Any]:
    data = model.model_dump(mode="json")
    _entities[(entity_type, entity_id)] = data
    return _entity(entity_type, entity_id, data)


def _vehicle_states() -> list[VehicleState]:
    return [VehicleState.model_validate(data) for (kind, _), data in _entities.items() if kind == "vehicle"]


def _refresh_risks() -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Replace transient trajectory risks with the current evaluation frame.

    Risk events receive a fresh identifier on each evaluation. Keeping every
    prior frame made long-running simulations grow an unbounded, stale risk
    set and caused the dashboard to select conflicts that no longer existed.
    """
    deletes = [
        {"entity_type": "risk", "entity_id": entity_id}
        for (kind, entity_id) in tuple(_entities)
        if kind == "risk"
    ]
    for deleted in deletes:
        _entities.pop(("risk", deleted["entity_id"]), None)
    upserts: list[dict[str, Any]] = []
    for risk in _risk_engine.evaluate_all(_vehicle_states()):
        incident_traces.record_risk(risk)
        upserts.append(_store_model("risk", risk.risk_id, risk))
    return upserts, deletes


async def ingest_vehicle_state(state: VehicleState) -> dict[str, Any]:
    prior_data = _entities.get(("vehicle", state.actor_id))
    prior = VehicleState.model_validate(prior_data) if prior_data else None
    fused = _position_fusion.fuse_with_previous(prior, state)
    upserts = [_store_model("vehicle", fused.actor_id, fused)]
    risk_upserts, risk_deletes = _refresh_risks()
    upserts.extend(risk_upserts)
    _notify(_world_delta("delta", upserts, risk_deletes))
    state_dict = upserts[0]["data"]
    try:
        from packages.event_bus.bus import get_event_bus

        bus = get_event_bus()
        if bus and bus.connected:
            await bus.publish("actor.state.updated", state_dict)
    except Exception:
        pass
    try:
        from packages.redis_store.actor_ttl import get_ttl_manager

        mgr = get_ttl_manager()
        if mgr:
            await mgr.touch(fused.actor_id, fused.model_dump_json())
    except Exception:
        pass
    try:
        from marga_observability.metrics import metrics as _m

        _m.actor_updates_total.labels(source="INGESTION").inc()
    except Exception:
        pass
    return {
        "entity": upserts[0],
        "trajectory": predict_trajectory(fused).model_dump(mode="json"),
        "risk_count": len(upserts) - 1,
    }


async def ingest_pedestrian_state(state: PedestrianState) -> dict[str, Any]:
    entity = _store_model("pedestrian", state.actor_id, state)
    _notify(_world_delta("delta", [entity]))
    return {"entity": entity}


async def ingest_hazard_observation(observation: HazardObservation) -> dict[str, Any]:
    try:
        hazard_type = HazardType(observation.hazard_type.upper())
    except ValueError:
        hazard_type = HazardType.OTHER
    ttl_s = (
        max(1, int((observation.expires_at - observation.timestamp_utc).total_seconds()))
        if observation.expires_at
        else 300
    )
    hazard = Hazard(
        hazard_id=observation.hazard_id,
        type=hazard_type,
        geometry={"type": "Point", "coordinates": [observation.position.lon, observation.position.lat]},
        severity=float(observation.evidence.get("severity", 0.5)),
        confidence=observation.confidence,
        first_seen=observation.timestamp_utc,
        last_seen=observation.timestamp_utc,
        ttl_s=ttl_s,
        source_ids=[observation.reporting_source, *observation.corroborating_sources],
        evidence_count=1 + len(observation.corroborating_sources),
        state=HazardState.CANDIDATE,
    )
    entity = _store_model("hazard", hazard.hazard_id, hazard)
    _notify(_world_delta("delta", [entity]))
    return {"entity": entity}


class IngestRequest(BaseModel):
    events: list[Any]


class PolicyFeedbackRequest(BaseModel):
    action: Literal["SLOW_DOWN_ADVISORY", "LOCAL_RELAY", "EARLY_WARNING"]
    reward: float = Field(ge=0.0, le=1.0)
    congestion_count: int = Field(ge=0)
    gps_uncertainty_m: float = Field(ge=0.0)
    connectivity: str = "FULL"
    decision_key: str


def _policy_context() -> PolicyContext:
    risks = [data for (kind, _), data in _entities.items() if kind == "risk"]
    highest = max(risks, key=lambda item: float(item.get("risk_score", 0.0)), default={})
    vehicles = [data for (kind, _), data in _entities.items() if kind == "vehicle"]
    uncertainty = max((float(item.get("position_uncertainty_m", 0.0)) for item in vehicles), default=0.0)
    return PolicyContext(
        congestion_count=len(vehicles),
        gps_uncertainty_m=uncertainty,
        connectivity=str((_connectivity_state or {}).get("mode", "FULL")),
        risk_severity=float(highest.get("severity", 0.0)),
        decision_key=str(highest.get("risk_id", "no-active-risk")),
    )


@router.get("/v1/policy/recommendation")
async def policy_recommendation() -> dict[str, object]:
    """Return an advisory response policy; this never replaces risk detection."""
    return _policy_learner.recommend(_policy_context())


@router.post("/v1/policy/feedback")
async def policy_feedback(feedback: PolicyFeedbackRequest) -> dict[str, object]:
    """Score a completed advisory outcome so the bandit can learn online."""
    context = PolicyContext(
        congestion_count=feedback.congestion_count,
        gps_uncertainty_m=feedback.gps_uncertainty_m,
        connectivity=feedback.connectivity,
        risk_severity=0.0,
        decision_key=feedback.decision_key,
    )
    return _policy_learner.record_feedback(context, feedback.action, feedback.reward)


@router.post("/v1/world-state/ingest")
async def ingest_legacy(req: IngestRequest) -> dict[str, int]:
    """Compatibility bridge for adapter envelopes; new callers use /v1/ingest."""
    updated = errors = 0
    for event in req.events:
        try:
            await ingest_vehicle_state(vehicle_from_adapter_event(event))
            updated += 1
        except Exception as exc:
            logger.debug("skipping invalid adapter event: %s", exc)
            errors += 1
    return {"updated": updated, "errors": errors, "total_actors": len(_vehicle_states())}


@router.post("/v1/ingest/vehicle-state", status_code=202)
async def ingest_vehicle(state: VehicleState) -> dict[str, Any]:
    return await ingest_vehicle_state(state)


@router.post("/v1/ingest/pedestrian-state", status_code=202)
async def ingest_pedestrian(state: PedestrianState) -> dict[str, Any]:
    return await ingest_pedestrian_state(state)


@router.post("/v1/ingest/hazard-observation", status_code=202)
async def ingest_hazard(observation: HazardObservation) -> dict[str, Any]:
    return await ingest_hazard_observation(observation)


@router.post("/v1/ingest/connectivity", status_code=202)
async def ingest_connectivity(event: ConnectivityEvent) -> dict[str, Any]:
    """Record a connectivity state transition and broadcast to WS subscribers."""
    global _connectivity_state
    _connectivity_state = event.model_dump(mode="json")
    _notify(_world_delta("delta", []))
    logger.info("connectivity: mode=%s affected=%s", event.mode, event.affected_actor_ids)
    return {"event_id": event.event_id, "mode": event.mode}


@router.post("/v1/ingest/position-quality", status_code=202)
async def ingest_position_quality(event: PositionQualityEvent) -> dict[str, Any]:
    """Record a GPS quality event; propagate uncertainty into the live actor state."""
    _position_quality[event.actor_id] = event.model_dump(mode="json")
    actor = _entities.get(("vehicle", event.actor_id))
    if actor is not None:
        actor["position_uncertainty_m"] = event.uncertainty_m
        _entities[("vehicle", event.actor_id)] = actor
        _notify(_world_delta("delta", [_entity("vehicle", event.actor_id, actor)]))
    logger.info("position_quality: actor=%s uncertainty=%.1fm", event.actor_id, event.uncertainty_m)
    return {"event_id": event.event_id, "actor_id": event.actor_id, "uncertainty_m": event.uncertainty_m}


@router.get("/v1/world-state/connectivity")
async def get_connectivity() -> dict[str, Any]:
    """Return the current connectivity state."""
    return _connectivity_state or {"mode": "FULL"}


@router.get("/v1/world-state/snapshot")
async def snapshot() -> dict[str, Any]:
    return _legacy_snapshot()


@router.get("/v1/incidents/{incident_id}/trace")
async def incident_trace(incident_id: str) -> dict[str, Any]:
    trace = incident_traces.get(incident_id)
    if trace is None:
        raise HTTPException(status_code=404, detail="incident trace not found")
    return trace


class GeoPoint(BaseModel):
    lat: float
    lon: float


class RerouteRequest(BaseModel):
    actor_id: str
    origin: GeoPoint
    destination: GeoPoint
    avoid_segment_ids: list[str] = Field(default_factory=list)


class RerouteResponse(BaseModel):
    actor_id: str
    route_geometry: list[GeoPoint]
    avoidance_reason: str
    estimated_delay_s: float
    resolved_alert_ids: list[str]


def _bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1, phi2, dlon = math.radians(lat1), math.radians(lat2), math.radians(lon2 - lon1)
    numerator = math.sin(dlon) * math.cos(phi2)
    denominator = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(dlon)
    return (math.degrees(math.atan2(numerator, denominator)) + 360) % 360


def _offset_point(lat: float, lon: float, bearing_deg: float, distance_m: float) -> tuple[float, float]:
    angular_distance, bearing = distance_m / 6_371_000.0, math.radians(bearing_deg)
    phi, lam = math.radians(lat), math.radians(lon)
    target_lat = math.asin(
        math.sin(phi) * math.cos(angular_distance) + math.cos(phi) * math.sin(angular_distance) * math.cos(bearing)
    )
    target_lon = lam + math.atan2(
        math.sin(bearing) * math.sin(angular_distance) * math.cos(phi),
        math.cos(angular_distance) - math.sin(phi) * math.sin(target_lat),
    )
    return math.degrees(target_lat), math.degrees(target_lon)


@router.post("/v1/world-state/reroute", response_model=RerouteResponse)
async def reroute(req: RerouteRequest) -> RerouteResponse:
    bearing = _bearing(req.origin.lat, req.origin.lon, req.destination.lat, req.destination.lon)
    lat, lon = _offset_point(
        (req.origin.lat + req.destination.lat) / 2,
        (req.origin.lon + req.destination.lon) / 2,
        (bearing + 90) % 360,
        150,
    )
    return RerouteResponse(
        actor_id=req.actor_id,
        route_geometry=[req.origin, GeoPoint(lat=lat, lon=lon), req.destination],
        avoidance_reason=(
            "road_closure" if not req.avoid_segment_ids else f"segments:{','.join(req.avoid_segment_ids[:3])}"
        ),
        estimated_delay_s=20.0,
        resolved_alert_ids=[],
    )


# ---------------------------------------------------------------------------
# Actor and signal command endpoints (Driver Console)
# ---------------------------------------------------------------------------

_signal_overrides: dict[str, dict[str, Any]] = {}


class ActorCommandRequest(BaseModel):
    action: Literal["set_speed", "stop", "resume"]
    speed_mps: float | None = None


@router.post("/v1/world-state/actors/{actor_id}/command")
async def actor_command(actor_id: str, cmd: ActorCommandRequest) -> dict[str, Any]:
    """Apply a speed or stop command to an actor in the live world state."""
    actor = _entities.get(("vehicle", actor_id))
    if actor is None:
        raise HTTPException(status_code=404, detail=f"Actor {actor_id!r} not in world state")
    if cmd.action == "set_speed" and cmd.speed_mps is not None:
        actor["speed_mps"] = round(max(0.0, float(cmd.speed_mps)), 3)
    elif cmd.action == "stop":
        actor["speed_mps"] = 0.0
    elif cmd.action == "resume":
        actor["speed_mps"] = 5.0
    _entities[("vehicle", actor_id)] = actor
    _notify(_world_delta("delta", [_entity("vehicle", actor_id, actor)]))
    logger.info("actor_command: actor=%s action=%s speed=%.1f", actor_id, cmd.action, actor.get("speed_mps", 0))
    return {"actor_id": actor_id, "applied": cmd.action, "speed_mps": actor.get("speed_mps")}


class SignalCommandRequest(BaseModel):
    phase: Literal["RED", "AMBER", "GREEN"]
    duration_s: float = 30.0


@router.post("/v1/world-state/signals/{signal_id}/command")
async def signal_command(signal_id: str, cmd: SignalCommandRequest) -> dict[str, Any]:
    """Override a traffic signal phase (operator command)."""
    _signal_overrides[signal_id] = {"phase": cmd.phase, "duration_s": cmd.duration_s}
    logger.info("signal_command: signal=%s phase=%s duration=%.0fs", signal_id, cmd.phase, cmd.duration_s)
    return {"signal_id": signal_id, "applied": cmd.phase, "duration_s": cmd.duration_s}


@router.websocket("/v1/world-state/stream")
async def stream(ws: WebSocket) -> None:
    """Emit WorldDelta snapshots/deltas that the dashboard store can apply."""
    await ws.accept()
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=64)
    _subscribers.append(queue)
    try:
        await ws.send_json(_snapshot_delta())
        while True:
            try:
                await ws.send_json(await asyncio.wait_for(queue.get(), timeout=30.0))
            except TimeoutError:
                await ws.send_json({"kind": "delta", "server_time": _now(), "upserts": [], "deletes": [], "ping": True})
    except WebSocketDisconnect:
        pass
    finally:
        if queue in _subscribers:
            _subscribers.remove(queue)
