"""Infrastructure state schemas: traffic signals, RSUs, toll gates, and other fixed assets."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

from .common import Position


class SignalPhase(str, Enum):
    """Traffic signal phase states."""

    red = "red"
    yellow = "yellow"
    green = "green"
    flashing_red = "flashing_red"
    flashing_yellow = "flashing_yellow"
    off = "off"


class InfrastructureType(str, Enum):
    """Categories of roadside infrastructure tracked by the platform."""

    traffic_signal = "traffic_signal"
    rsu = "rsu"
    toll_gate = "toll_gate"
    speed_camera = "speed_camera"
    variable_message_sign = "variable_message_sign"


class InfrastructureState(BaseModel):
    """Current operational state of a piece of roadside infrastructure."""

    schema_version: str = Field("1.0", description="Schema version string")
    infrastructure_id: str = Field(..., description="Unique infrastructure element identifier")
    timestamp_utc: datetime = Field(..., description="UTC timestamp of this state snapshot")
    infrastructure_type: InfrastructureType = Field(..., description="Class of infrastructure")
    position: Position = Field(..., description="Fixed geographic position (WGS-84)")
    signal_phase: Optional[SignalPhase] = Field(
        None, description="Current signal phase (traffic signals only)"
    )
    phase_remaining_s: Optional[float] = Field(
        None, ge=0.0, description="Seconds until current phase ends"
    )
    operational: bool = Field(True, description="Whether the element is currently operational")
    source: str = Field(..., description="Data origin identifier")
    scenario_run_id: Optional[str] = Field(None, description="Simulation scenario run identifier")
