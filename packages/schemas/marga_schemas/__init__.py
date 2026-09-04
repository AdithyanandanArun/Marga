"""Marga canonical schemas — the single source of truth for all domain entities and events."""

from marga_schemas.envelope import EventEnvelope
from marga_schemas.common import (
    ActorType,
    ConnectivityState,
    GeoPoint,
    PositionMethod,
    SchemaVersioned,
    Source,
)
from marga_schemas.hazard import (
    Hazard,
    HazardObservation,
    HazardState,
    HazardType,
)
from marga_schemas.trust import (
    SignedMessage,
    TrustAssessment,
    TrustLevel,
)
from marga_schemas.alert import Alert, AlertPriority, AlertState
from marga_schemas.vehicle import VehicleState
from marga_schemas.messaging import (
    MessagePriority,
    QueueClass,
    V2XMessage,
)

__all__ = [
    "ActorType",
    "Alert",
    "AlertPriority",
    "AlertState",
    "ConnectivityState",
    "EventEnvelope",
    "GeoPoint",
    "Hazard",
    "HazardObservation",
    "HazardState",
    "HazardType",
    "MessagePriority",
    "PositionMethod",
    "QueueClass",
    "SchemaVersioned",
    "SignedMessage",
    "Source",
    "TrustAssessment",
    "TrustLevel",
    "V2XMessage",
    "VehicleState",
]
