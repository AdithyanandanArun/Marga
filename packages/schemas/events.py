"""Canonical event types and the CanonicalEvent envelope for the Marga event bus."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Event type constants
# ---------------------------------------------------------------------------

ACTOR_STATE_UPDATED = "actor.state.updated"
INFRASTRUCTURE_SIGNAL_UPDATED = "infrastructure.signal.updated"
HAZARD_OBSERVED = "hazard.observed"
HAZARD_UPDATED = "hazard.updated"
POSITION_ESTIMATE_UPDATED = "position.estimate.updated"
TRUST_ASSESSMENT_UPDATED = "trust.assessment.updated"
RISK_DETECTED = "risk.detected"
ALERT_ISSUED = "alert.issued"
ROAD_STATE_UPDATED = "road.state.updated"
SCENARIO_STARTED = "scenario.started"
SCENARIO_STOPPED = "scenario.stopped"

# ---------------------------------------------------------------------------
# Canonical event envelope
# ---------------------------------------------------------------------------


class CanonicalEvent(BaseModel):
    """Envelope wrapping any domain payload for publication on the Marga event bus."""

    event_type: str = Field(..., description="Event type identifier (dot-separated namespace)")
    schema_version: str = Field("1.0", description="Schema version string")
    event_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Unique event identifier (UUID)",
    )
    timestamp_utc: datetime = Field(..., description="UTC timestamp of the event")
    source: str = Field(..., description="Component that emitted this event")
    trace_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Distributed trace identifier (UUID)",
    )
    payload: dict[str, Any] = Field(..., description="Event payload (schema varies by event_type)")

    # ------------------------------------------------------------------
    # Factory methods
    # ------------------------------------------------------------------

    @classmethod
    def from_vehicle_state(cls, vs: Any) -> "CanonicalEvent":
        """Wrap a VehicleState as an ACTOR_STATE_UPDATED event."""
        return cls(
            event_type=ACTOR_STATE_UPDATED,
            timestamp_utc=vs.timestamp_utc,
            source=vs.source,
            trace_id=vs.trace_id,
            payload=vs.model_dump(mode="json"),
        )

    @classmethod
    def from_pedestrian_state(cls, ps: Any) -> "CanonicalEvent":
        """Wrap a PedestrianState as an ACTOR_STATE_UPDATED event."""
        return cls(
            event_type=ACTOR_STATE_UPDATED,
            timestamp_utc=ps.timestamp_utc,
            source=ps.source,
            trace_id=ps.trace_id,
            payload=ps.model_dump(mode="json"),
        )

    @classmethod
    def from_infrastructure_state(cls, is_: Any) -> "CanonicalEvent":
        """Wrap an InfrastructureState as an INFRASTRUCTURE_SIGNAL_UPDATED event."""
        return cls(
            event_type=INFRASTRUCTURE_SIGNAL_UPDATED,
            timestamp_utc=is_.timestamp_utc,
            source=is_.source,
            trace_id=str(uuid.uuid4()),
            payload=is_.model_dump(mode="json"),
        )

    @classmethod
    def from_road_state(cls, rs: Any) -> "CanonicalEvent":
        """Wrap a RoadState as a ROAD_STATE_UPDATED event."""
        return cls(
            event_type=ROAD_STATE_UPDATED,
            timestamp_utc=rs.timestamp_utc,
            source=rs.source,
            trace_id=str(uuid.uuid4()),
            payload=rs.model_dump(mode="json"),
        )
