"""Versioned safety policy configuration.

All thresholds and tuning parameters are defined here as versioned
configuration, not embedded in detector logic. This enables:
- Reproducible safety decisions (config version in every alert)
- Tuning without code changes
- Audit trail of threshold changes
"""

from __future__ import annotations

from pydantic import BaseModel, Field

POLICY_CONFIG_VERSION = "0.1.0"


class WrongWayConfig(BaseModel):
    """Configuration for wrong-way driving detection."""

    alignment_threshold: float = Field(
        default=-0.5,
        description="Heading alignment below this = wrong way. Range [-1, 1].",
    )
    min_persistence_updates: int = Field(
        default=3,
        description="Required consecutive wrong-way observations before alerting.",
    )
    min_map_match_confidence: float = Field(
        default=0.6,
        description="Minimum road map-match confidence to evaluate wrong-way.",
    )
    min_speed_mps: float = Field(
        default=1.0,
        description="Minimum speed to consider for wrong-way (filter stationary noise).",
    )


class EmergencyBrakingConfig(BaseModel):
    """Configuration for emergency braking detection."""

    deceleration_threshold_mps2: float = Field(
        default=-4.0,
        description="Acceleration below this triggers emergency braking.",
    )
    min_duration_s: float = Field(
        default=0.5,
        description="Minimum duration of hard braking to qualify.",
    )
    alert_ttl_s: float = Field(
        default=15.0,
        description="Time-to-live for braking alerts.",
    )
    closing_time_threshold_s: float = Field(
        default=10.0,
        description="Only alert actors within this closing time.",
    )


class StalledVehicleConfig(BaseModel):
    """Configuration for stalled vehicle detection."""

    max_speed_mps: float = Field(
        default=0.5,
        description="Speed below this is considered stopped.",
    )
    min_stopped_duration_s: float = Field(
        default=30.0,
        description="Minimum stopped duration before considering stalled.",
    )
    surrounding_flow_threshold_mps: float = Field(
        default=2.0,
        description="Surrounding traffic must have this speed to differentiate from congestion.",
    )
    lane_occupancy_required: bool = Field(
        default=True,
        description="Must be occupying a travel lane (not shoulder).",
    )


class BlindIntersectionConfig(BaseModel):
    """Configuration for blind intersection risk detection."""

    eta_overlap_threshold_s: float = Field(
        default=5.0,
        description="ETA overlap window to consider intersection conflict.",
    )
    min_confidence: float = Field(
        default=0.3,
        description="Minimum confidence to report blind intersection risk.",
    )
    approach_distance_m: float = Field(
        default=100.0,
        description="Distance from intersection to start evaluating.",
    )


class BlindCurveConfig(BaseModel):
    """Configuration for blind curve risk detection."""

    network_distance_threshold_m: float = Field(
        default=150.0,
        description="Along-road network distance to scan for opposing actors.",
    )
    min_closing_speed_mps: float = Field(
        default=5.0,
        description="Minimum relative closing speed to trigger risk.",
    )
    curvature_threshold_deg: float = Field(
        default=45.0,
        description="Road curvature above this is considered 'blind'.",
    )


class EmergencyVehicleConfig(BaseModel):
    """Configuration for emergency vehicle priority."""

    yield_alert_distance_m: float = Field(
        default=500.0,
        description="Distance at which yield alerts are sent to affected actors.",
    )
    credential_required: bool = Field(
        default=True,
        description="Whether trust verification is required for emergency privileges.",
    )
    heartbeat_timeout_s: float = Field(
        default=30.0,
        description="Time after which emergency status expires without heartbeat.",
    )
    corridor_relevance_only: bool = Field(
        default=True,
        description="Only alert actors in the same/relevant corridor.",
    )


class AnimalConflictConfig(BaseModel):
    """Configuration for animal/non-connected actor conflict detection."""

    max_animal_speed_mps: dict[str, float] = Field(
        default_factory=lambda: {
            "cow": 5.0,
            "dog": 12.0,
            "horse": 15.0,
            "unknown": 8.0,
            "default": 8.0,
        },
        description="Class-specific maximum speed assumptions for reachable region.",
    )
    turn_uncertainty_deg: float = Field(
        default=90.0,
        description="Maximum turn angle for reachable region expansion.",
    )
    min_detector_confidence: float = Field(
        default=0.3,
        description="Minimum observation confidence to create risk.",
    )
    track_prediction_timeout_s: float = Field(
        default=5.0,
        description="Continue track prediction after observation disappears.",
    )
    low_confidence_alert_suppression: float = Field(
        default=0.5,
        description="Single detection below this confidence never triggers critical alert.",
    )


class RoadHazardConfig(BaseModel):
    """Configuration for road hazard risk assessment."""

    default_ttl_s: dict[str, int] = Field(
        default_factory=lambda: {
            "POTHOLE": 86400,
            "BUMP": 86400,
            "DEBRIS": 3600,
            "FLOOD": 7200,
            "CONSTRUCTION": 86400,
            "LANE_CLOSURE": 86400,
            "ACCIDENT": 3600,
            "LOW_VISIBILITY": 1800,
            "ROAD_NARROWING": 86400,
            "OTHER": 3600,
        },
    )
    approach_warning_distance_m: float = Field(
        default=200.0,
        description="Distance at which approaching vehicles are warned.",
    )
    speed_severity_factor: float = Field(
        default=0.02,
        description="Severity multiplier per m/s of approaching speed.",
    )


class AlertPrioritizationConfig(BaseModel):
    """Configuration for alert prioritization and suppression."""

    max_concurrent_alerts: int = Field(
        default=5,
        description="Maximum active alerts per actor at once.",
    )
    suppression_window_s: float = Field(
        default=10.0,
        description="Minimum time between repeated alerts for the same risk.",
    )
    critical_preempts_advisory: bool = Field(
        default=True,
        description="Critical collision alerts suppress lower-priority hazard alerts.",
    )
    hysteresis_threshold: float = Field(
        default=0.1,
        description="Risk score must change by this much to update an existing alert.",
    )


class HazardFusionConfig(BaseModel):
    """Configuration for cooperative hazard fusion."""

    spatial_match_radius_m: float = Field(
        default=50.0,
        description="Maximum distance for associating observations to existing hazards.",
    )
    association_score_threshold: float = Field(
        default=0.5,
        description="Minimum association score to link observation to existing hazard.",
    )
    confidence_decay_rate: float = Field(
        default=0.01,
        description="Confidence decay per second without fresh evidence.",
    )
    max_source_weight: float = Field(
        default=0.3,
        description="Max contribution from a single source to prevent single-source dominance.",
    )
    promotion_confidence: float = Field(
        default=0.7,
        description="Confidence threshold to promote from CANDIDATE to VERIFIED.",
    )
    stale_threshold_s: float = Field(
        default=300.0,
        description="Seconds without observation before marking STALE.",
    )


class PolicyConfig(BaseModel):
    """Root configuration for all safety policies."""

    version: str = POLICY_CONFIG_VERSION
    wrong_way: WrongWayConfig = Field(default_factory=WrongWayConfig)
    emergency_braking: EmergencyBrakingConfig = Field(default_factory=EmergencyBrakingConfig)
    stalled_vehicle: StalledVehicleConfig = Field(default_factory=StalledVehicleConfig)
    blind_intersection: BlindIntersectionConfig = Field(default_factory=BlindIntersectionConfig)
    blind_curve: BlindCurveConfig = Field(default_factory=BlindCurveConfig)
    emergency_vehicle: EmergencyVehicleConfig = Field(default_factory=EmergencyVehicleConfig)
    animal_conflict: AnimalConflictConfig = Field(default_factory=AnimalConflictConfig)
    road_hazard: RoadHazardConfig = Field(default_factory=RoadHazardConfig)
    alert_prioritization: AlertPrioritizationConfig = Field(default_factory=AlertPrioritizationConfig)
    hazard_fusion: HazardFusionConfig = Field(default_factory=HazardFusionConfig)


class SafetyPolicyRegistry:
    """Registry for safety detectors and policies.

    Provides a central place to register, discover, and instantiate
    all active safety feature modules.
    """

    def __init__(self, config: PolicyConfig | None = None) -> None:
        self.config = config or PolicyConfig()
        self._detectors: dict[str, type] = {}
        self._policies: dict[str, type] = {}

    def register_detector(self, name: str, cls: type) -> None:
        self._detectors[name] = cls

    def register_policy(self, name: str, cls: type) -> None:
        self._policies[name] = cls

    def get_detector_names(self) -> list[str]:
        return list(self._detectors.keys())

    def get_policy_names(self) -> list[str]:
        return list(self._policies.keys())

    def create_detector(self, name: str, **kwargs: object) -> object:
        if name not in self._detectors:
            raise KeyError(f"Unknown detector: {name}")
        return self._detectors[name](config=self.config, **kwargs)

    def create_policy(self, name: str, **kwargs: object) -> object:
        if name not in self._policies:
            raise KeyError(f"Unknown policy: {name}")
        return self._policies[name](config=self.config, **kwargs)
