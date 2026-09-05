"""Canonical domain model entities for Marga V2X platform.

All adapters normalize inputs into these canonical types. Core services
depend only on these entities - never on SUMO-specific, frontend, or
vendor-specific types.

Invariants:
- Timestamps: UTC, RFC 3339
- Speeds: m/s
- Headings: degrees clockwise from true north, [0, 360)
- Confidence: [0, 1]
- Every entity carries schema_version and source metadata
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator

SCHEMA_VERSION = "0.1.0"


class SourceType(str, enum.Enum):
    SIMULATION = "SIMULATION"
    PHONE = "PHONE"
    OBU = "OBU"
    RSU = "RSU"
    VEHICLE_API = "VEHICLE_API"
    MANUAL = "MANUAL"
    VISION = "VISION"


class ActorType(str, enum.Enum):
    CAR = "CAR"
    BIKE = "BIKE"
    AUTO = "AUTO"
    BUS = "BUS"
    TRUCK = "TRUCK"
    AMBULANCE = "AMBULANCE"
    FIRE_TRUCK = "FIRE_TRUCK"
    POLICE = "POLICE"
    PEDESTRIAN = "PEDESTRIAN"
    CYCLIST = "CYCLIST"
    ANIMAL = "ANIMAL"
    OTHER = "OTHER"


class HazardType(str, enum.Enum):
    POTHOLE = "POTHOLE"
    BUMP = "BUMP"
    DEBRIS = "DEBRIS"
    FLOOD = "FLOOD"
    LANDSLIDE = "LANDSLIDE"
    ANIMAL = "ANIMAL"
    STALLED_VEHICLE = "STALLED_VEHICLE"
    CONSTRUCTION = "CONSTRUCTION"
    LANE_CLOSURE = "LANE_CLOSURE"
    ACCIDENT = "ACCIDENT"
    LOW_VISIBILITY = "LOW_VISIBILITY"
    ROAD_NARROWING = "ROAD_NARROWING"
    OTHER = "OTHER"


class HazardState(str, enum.Enum):
    CANDIDATE = "CANDIDATE"
    VERIFIED = "VERIFIED"
    STALE = "STALE"
    EXPIRED = "EXPIRED"


class RiskType(str, enum.Enum):
    COLLISION = "COLLISION"
    REAR_END = "REAR_END"
    HEAD_ON = "HEAD_ON"
    INTERSECTION_CONFLICT = "INTERSECTION_CONFLICT"
    BLIND_CURVE = "BLIND_CURVE"
    BLIND_INTERSECTION = "BLIND_INTERSECTION"
    WRONG_WAY = "WRONG_WAY"
    EMERGENCY_BRAKING = "EMERGENCY_BRAKING"
    STALLED_VEHICLE = "STALLED_VEHICLE"
    ANIMAL_CROSSING = "ANIMAL_CROSSING"
    PEDESTRIAN_CONFLICT = "PEDESTRIAN_CONFLICT"
    ROAD_HAZARD = "ROAD_HAZARD"
    EMERGENCY_VEHICLE = "EMERGENCY_VEHICLE"
    ROAD_NARROWING = "ROAD_NARROWING"


class AlertLevel(str, enum.Enum):
    CRITICAL = "CRITICAL"
    WARNING = "WARNING"
    ADVISORY = "ADVISORY"
    INFORMATIONAL = "INFORMATIONAL"


class AlertStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    RESOLVED = "RESOLVED"
    EXPIRED = "EXPIRED"
    SUPPRESSED = "SUPPRESSED"


class Position(BaseModel):
    """Geographic position with optional altitude."""

    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    altitude_m: float | None = None


class VehicleState(BaseModel):
    """Canonical vehicle state - the primary actor entity."""

    schema_version: str = SCHEMA_VERSION
    actor_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    actor_type: ActorType = ActorType.CAR
    ts: datetime
    position: Position
    position_uncertainty_m: float = Field(ge=0)
    speed_mps: float = Field(ge=0)
    acceleration_mps2: float | None = None
    heading_deg: float = Field(ge=0, lt=360)
    yaw_rate_dps: float | None = None
    road_segment_id: str | None = None
    lane_id: str | None = None
    source: SourceType = SourceType.SIMULATION
    trust_context_id: str | None = None
    capabilities: list[str] = Field(default_factory=list)

    @field_validator("heading_deg")
    @classmethod
    def normalize_heading(cls, v: float) -> float:
        return v % 360


class PedestrianState(BaseModel):
    """Canonical pedestrian state."""

    schema_version: str = SCHEMA_VERSION
    actor_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    actor_type: ActorType = ActorType.PEDESTRIAN
    ts: datetime
    position: Position
    position_uncertainty_m: float = Field(ge=0)
    speed_mps: float = Field(ge=0)
    heading_deg: float = Field(ge=0, lt=360)
    road_segment_id: str | None = None
    source: SourceType = SourceType.SIMULATION
    trust_context_id: str | None = None


# ActorState is the canonical union consumed by world projections such as the
# mobility graph. It replaces no existing wire type; vehicle and pedestrian
# payloads remain individually discriminated at service boundaries.
ActorState = VehicleState | PedestrianState


class Hazard(BaseModel):
    """Canonical hazard entity with lifecycle and fusion state."""

    schema_version: str = SCHEMA_VERSION
    hazard_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    type: HazardType
    geometry: dict[str, Any] = Field(
        description="GeoJSON geometry: Point, LineString, or Polygon"
    )
    severity: float = Field(ge=0, le=1)
    confidence: float = Field(ge=0, le=1)
    first_seen: datetime
    last_seen: datetime
    ttl_s: int = Field(gt=0)
    source_ids: list[str] = Field(default_factory=list)
    evidence_count: int = Field(ge=0, default=0)
    state: HazardState = HazardState.CANDIDATE
    road_segment_id: str | None = None


class DynamicActorObservation(BaseModel):
    """Observation of a non-connected dynamic actor (animal, unconnected pedestrian, etc.)."""

    schema_version: str = SCHEMA_VERSION
    observation_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    actor_class: str  # e.g. "cow", "dog", "pedestrian", "cyclist", "unknown"
    ts: datetime
    position: Position
    position_uncertainty_m: float = Field(ge=0)
    speed_mps: float | None = None
    heading_deg: float | None = None
    detector_confidence: float = Field(ge=0, le=1)
    source_id: str
    source: SourceType = SourceType.SIMULATION
    track_id: str | None = None


class RiskEvent(BaseModel):
    """A detected risk/conflict event with evidence."""

    schema_version: str = SCHEMA_VERSION
    risk_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    type: RiskType
    ts: datetime
    affected_actor_ids: list[str]
    time_to_conflict_s: float | None = None
    min_predicted_distance_m: float | None = None
    severity: float = Field(ge=0, le=1)
    confidence: float = Field(ge=0, le=1)
    risk_score: float = Field(ge=0, le=1)
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    expires_at: datetime | None = None
    geometry: dict[str, Any] | None = None
    road_segment_id: str | None = None
    policy_version: str = SCHEMA_VERSION


class Alert(BaseModel):
    """A prioritized, human-facing safety alert with full evidence."""

    schema_version: str = SCHEMA_VERSION
    alert_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    risk_id: str
    level: AlertLevel
    status: AlertStatus = AlertStatus.ACTIVE
    title: str
    description: str
    ts: datetime
    affected_actor_ids: list[str]
    confidence: float = Field(ge=0, le=1)
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    time_to_conflict_s: float | None = None
    expires_at: datetime | None = None
    target_audience: list[str] = Field(
        default_factory=list,
        description="Actor IDs or groups that should receive this alert",
    )
    policy_version: str = SCHEMA_VERSION
    suppression_key: str | None = None


class InfrastructureState(BaseModel):
    """Canonical traffic infrastructure state (signals, signs)."""

    schema_version: str = SCHEMA_VERSION
    infrastructure_id: str
    type: str  # TRAFFIC_SIGNAL, SIGN, RSU, etc.
    ts: datetime
    position: Position
    state: dict[str, Any] = Field(default_factory=dict)
    source: SourceType = SourceType.SIMULATION


class TrafficSignalState(BaseModel):
    """Traffic signal phase state at an intersection."""

    schema_version: str = SCHEMA_VERSION
    signal_id: str
    intersection_id: str
    ts: datetime
    position: Position
    current_phase: str
    phase_remaining_s: float | None = None
    movements: dict[str, str] = Field(
        default_factory=dict,
        description="Movement direction -> signal color (GREEN/YELLOW/RED)",
    )
    source: SourceType = SourceType.SIMULATION


class SignalPriorityRequest(BaseModel):
    """Request for traffic signal priority (e.g. emergency vehicle)."""

    schema_version: str = SCHEMA_VERSION
    request_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    intersection_id: str
    desired_movement: str
    eta_window_s: tuple[float, float]
    requester_id: str
    credential_ref: str
    emergency_type: str
    expires_at: datetime
    ts: datetime


class RoadState(BaseModel):
    """Canonical road segment state (closures, narrowing, conditions)."""

    schema_version: str = SCHEMA_VERSION
    road_segment_id: str
    ts: datetime
    condition: str  # NORMAL, NARROWED, CLOSED, CONSTRUCTION, FLOODED
    lanes_available: int | None = None
    speed_limit_mps: float | None = None
    geometry: dict[str, Any] | None = None
    source: SourceType = SourceType.SIMULATION


class ConnectivityMode(str, enum.Enum):
    FULL = "FULL"
    DIRECT_ONLY = "DIRECT_ONLY"
    INTERMITTENT = "INTERMITTENT"
    ISOLATED = "ISOLATED"


class ConnectivityEvent(BaseModel):
    """Canonical connectivity-state change event (internet / direct V2X)."""

    schema_version: str = SCHEMA_VERSION
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    ts: datetime
    mode: ConnectivityMode
    affected_actor_ids: list[str] = Field(default_factory=list)
    v2x_range_m: float | None = None
    source: SourceType = SourceType.SIMULATION


class PositionQualityEvent(BaseModel):
    """Canonical GPS/position-quality degradation event for one actor."""

    schema_version: str = SCHEMA_VERSION
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    ts: datetime
    actor_id: str
    uncertainty_m: float = Field(ge=0)
    hdop: float | None = None
    satellites: int | None = None
    source: SourceType = SourceType.SIMULATION
