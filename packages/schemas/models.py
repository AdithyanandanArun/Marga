"""Canonical, versioned domain models for adapters and core services."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, JsonValue, field_validator, model_validator

from packages.geo import normalize_heading_deg

from .contracts import CONTRACT_VERSION, MODEL_CONFIG

SchemaVersion = Annotated[str, Field(pattern=r"^v[0-9]+(?:\.[0-9]+)?$")]
Confidence = Annotated[float, Field(ge=0.0, le=1.0)]


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamps must include a UTC offset")
    return value.astimezone(UTC)


class ActorType(StrEnum):
    CAR = "CAR"
    BIKE = "BIKE"
    AUTO = "AUTO"
    BUS = "BUS"
    TRUCK = "TRUCK"
    AMBULANCE = "AMBULANCE"
    PEDESTRIAN = "PEDESTRIAN"
    OTHER = "OTHER"


class Source(StrEnum):
    SIMULATION = "SIMULATION"
    PHONE = "PHONE"
    OBU = "OBU"
    RSU = "RSU"
    VEHICLE_API = "VEHICLE_API"
    CAMERA = "CAMERA"
    MAP = "MAP"
    OTHER = "OTHER"


class HazardType(StrEnum):
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
    OTHER = "OTHER"


class HazardState(StrEnum):
    CANDIDATE = "CANDIDATE"
    VERIFIED = "VERIFIED"
    STALE = "STALE"
    EXPIRED = "EXPIRED"


class DynamicActorClass(StrEnum):
    ANIMAL = "ANIMAL"
    DEBRIS = "DEBRIS"
    UNKNOWN_ROAD_USER = "UNKNOWN_ROAD_USER"


class PositionMethod(StrEnum):
    GNSS = "GNSS"
    FUSED = "FUSED"
    DEAD_RECKONED = "DEAD_RECKONED"
    MAP_MATCHED = "MAP_MATCHED"


class RiskType(StrEnum):
    GENERIC_CONFLICT = "GENERIC_CONFLICT"
    COLLISION = "COLLISION"
    INTERSECTION_CONFLICT = "INTERSECTION_CONFLICT"
    FOLLOWING_CONFLICT = "FOLLOWING_CONFLICT"


class AlertPriority(StrEnum):
    INFO = "INFO"
    ADVISORY = "ADVISORY"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


class CanonicalModel(BaseModel):
    model_config = MODEL_CONFIG
    schema_version: SchemaVersion = CONTRACT_VERSION


class GeoPoint(BaseModel):
    """WGS84 point used at API boundaries; metric math belongs in ``packages.geo``."""

    model_config = MODEL_CONFIG
    lat: Annotated[float, Field(ge=-90.0, le=90.0)]
    lon: Annotated[float, Field(ge=-180.0, le=180.0)]
    altitude_m: float | None = None


class GeoJSONPoint(BaseModel):
    model_config = MODEL_CONFIG
    type: Literal["Point"] = "Point"
    coordinates: tuple[
        Annotated[float, Field(ge=-180, le=180)], Annotated[float, Field(ge=-90, le=90)]
    ]


class GeoJSONLineString(BaseModel):
    model_config = MODEL_CONFIG
    type: Literal["LineString"] = "LineString"
    coordinates: tuple[
        tuple[Annotated[float, Field(ge=-180, le=180)], Annotated[float, Field(ge=-90, le=90)]], ...
    ]

    @model_validator(mode="after")
    def has_two_positions(self) -> GeoJSONLineString:
        if len(self.coordinates) < 2:
            raise ValueError("a LineString requires at least two positions")
        return self


class GeoJSONPolygon(BaseModel):
    model_config = MODEL_CONFIG
    type: Literal["Polygon"] = "Polygon"
    coordinates: tuple[
        tuple[
            tuple[Annotated[float, Field(ge=-180, le=180)], Annotated[float, Field(ge=-90, le=90)]],
            ...,
        ],
        ...,
    ]

    @model_validator(mode="after")
    def has_closed_rings(self) -> GeoJSONPolygon:
        if not self.coordinates:
            raise ValueError("a Polygon requires at least one ring")
        for ring in self.coordinates:
            if len(ring) < 4 or ring[0] != ring[-1]:
                raise ValueError("each Polygon ring requires four positions and must be closed")
        return self


Geometry = GeoJSONPoint | GeoJSONLineString | GeoJSONPolygon


class VehicleState(CanonicalModel):
    actor_id: str = Field(min_length=1, max_length=256)
    actor_type: ActorType
    ts: datetime
    position: GeoPoint
    position_uncertainty_m: Annotated[float, Field(ge=0.0)]
    speed_mps: Annotated[float, Field(ge=0.0)]
    acceleration_mps2: float | None = None
    heading_deg: float
    yaw_rate_dps: float | None = None
    road_segment_id: str | None = None
    lane_id: str | None = None
    source: Source
    trust_context_id: str | None = None
    capabilities: tuple[str, ...] = ()

    _timestamp_utc = field_validator("ts")(_utc)
    _heading_normalized = field_validator("heading_deg")(normalize_heading_deg)


class PedestrianState(CanonicalModel):
    actor_id: str = Field(min_length=1, max_length=256)
    ts: datetime
    position: GeoPoint
    position_uncertainty_m: Annotated[float, Field(ge=0.0)]
    speed_mps: Annotated[float, Field(ge=0.0)]
    heading_deg: float
    source: Source
    confidence: Confidence
    path_hint: str | None = None
    road_context: Literal["SIDEWALK", "CROSSWALK", "ROADWAY", "UNKNOWN"] = "UNKNOWN"

    _timestamp_utc = field_validator("ts")(_utc)
    _heading_normalized = field_validator("heading_deg")(normalize_heading_deg)


class Hazard(CanonicalModel):
    hazard_id: UUID = Field(default_factory=uuid4)
    type: HazardType
    geometry: Geometry = Field(discriminator="type")
    severity: Confidence
    confidence: Confidence
    first_seen: datetime
    last_seen: datetime
    ttl_s: Annotated[int, Field(gt=0)]
    source_ids: tuple[str, ...] = Field(min_length=1)
    evidence_count: Annotated[int, Field(ge=0)] = 0
    state: HazardState = HazardState.CANDIDATE

    _first_seen_utc = field_validator("first_seen")(_utc)
    _last_seen_utc = field_validator("last_seen")(_utc)

    @model_validator(mode="after")
    def observed_in_order(self) -> Hazard:
        if self.last_seen < self.first_seen:
            raise ValueError("last_seen cannot precede first_seen")
        return self


class DynamicActorObservation(CanonicalModel):
    observation_id: UUID = Field(default_factory=uuid4)
    actor_class: DynamicActorClass
    ts: datetime
    position: GeoPoint
    position_uncertainty_m: Annotated[float, Field(ge=0.0)]
    detector_confidence: Confidence
    source_id: str = Field(min_length=1)
    source_type: Source
    subtype: str | None = None
    velocity_east_mps: float | None = None
    velocity_north_mps: float | None = None
    heading_deg: float | None = None

    _timestamp_utc = field_validator("ts")(_utc)
    _heading_normalized = field_validator("heading_deg")(
        lambda value: normalize_heading_deg(value) if value is not None else value
    )


class PositionEstimate(CanonicalModel):
    actor_id: str = Field(min_length=1, max_length=256)
    estimate_id: UUID = Field(default_factory=uuid4)
    ts: datetime
    position: GeoPoint
    velocity_east_mps: float
    velocity_north_mps: float
    uncertainty_radius_m: Annotated[float, Field(ge=0.0)]
    confidence: Confidence
    method: PositionMethod
    source_event_ids: tuple[UUID, ...] = ()

    _timestamp_utc = field_validator("ts")(_utc)


class RiskEvent(CanonicalModel):
    risk_id: UUID = Field(default_factory=uuid4)
    type: RiskType = RiskType.GENERIC_CONFLICT
    detected_at: datetime
    actor_ids: tuple[str, ...] = Field(min_length=2)
    severity: Confidence
    confidence: Confidence
    time_to_conflict_s: Annotated[float | None, Field(ge=0.0)] = None
    minimum_separation_m: Annotated[float | None, Field(ge=0.0)] = None
    evidence: dict[str, JsonValue] = Field(default_factory=dict)
    policy_version: str = "v1"

    _detected_at_utc = field_validator("detected_at")(_utc)


class Alert(CanonicalModel):
    alert_id: UUID = Field(default_factory=uuid4)
    risk_id: UUID
    issued_at: datetime
    priority: AlertPriority
    audience_actor_ids: tuple[str, ...] = Field(min_length=1)
    confidence: Confidence
    summary: str = Field(min_length=1, max_length=500)
    evidence: dict[str, JsonValue] = Field(default_factory=dict)
    expires_at: datetime | None = None

    _issued_at_utc = field_validator("issued_at")(_utc)
    _expires_at_utc = field_validator("expires_at")(_utc)

    @model_validator(mode="after")
    def expiry_follows_issue(self) -> Alert:
        if self.expires_at is not None and self.expires_at < self.issued_at:
            raise ValueError("expires_at cannot precede issued_at")
        return self
