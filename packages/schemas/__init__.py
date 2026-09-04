"""Versioned canonical contracts shared by Marga services.

Services must import these public names rather than adapter-specific payloads.
"""

from .contracts import CONTRACT_VERSION
from .events import EventEnvelope, EventType
from .models import (
    ActorType,
    Alert,
    AlertPriority,
    DynamicActorClass,
    DynamicActorObservation,
    GeoJSONLineString,
    GeoJSONPoint,
    GeoJSONPolygon,
    GeoPoint,
    Hazard,
    HazardState,
    HazardType,
    PedestrianState,
    PositionEstimate,
    PositionMethod,
    RiskEvent,
    RiskType,
    Source,
    VehicleState,
)

__all__ = [
    "CONTRACT_VERSION",
    "ActorType",
    "Alert",
    "AlertPriority",
    "DynamicActorClass",
    "DynamicActorObservation",
    "EventEnvelope",
    "EventType",
    "GeoJSONLineString",
    "GeoJSONPoint",
    "GeoJSONPolygon",
    "GeoPoint",
    "Hazard",
    "HazardState",
    "HazardType",
    "PedestrianState",
    "PositionEstimate",
    "PositionMethod",
    "RiskEvent",
    "RiskType",
    "Source",
    "VehicleState",
]
