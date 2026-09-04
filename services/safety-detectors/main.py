"""Marga Safety Detectors Service.

FastAPI service for evaluating safety detectors, generating alerts,
and fusing hazard observations. All detectors are loaded dynamically
from the detectors/ subdirectory at startup.

The service handles three distinct component types:
- SafetyDetector subclasses: standard detectors with an ``evaluate(world_state)`` API
- AlertPrioritizer (SafetyPolicy): converts RiskEvents into prioritized Alerts
- HazardFusionEngine: fuses multi-source hazard observations into canonical hazards
"""

from __future__ import annotations

import importlib
import importlib.util
import logging
import sys
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Path setup: the service directory uses a hyphen ("safety-detectors") which
# is not valid in Python imports.  We ensure the project root is on sys.path
# so that `packages.*` imports work, and we use importlib to load detector
# modules from the local detectors/ directory.
# ---------------------------------------------------------------------------
_PROJECT_ROOT = str(Path(__file__).resolve().parents[2])
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

_SERVICE_DIR = Path(__file__).resolve().parent
_DETECTORS_DIR = _SERVICE_DIR / "detectors"

from packages.schemas.canonical import Alert, AlertLevel, Hazard, RiskEvent  # noqa: E402
from packages.safety_policies.base import SafetyDetector  # noqa: E402
from packages.safety_policies.config import PolicyConfig, SafetyPolicyRegistry  # noqa: E402

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)
logger = logging.getLogger("marga.safety-detectors")

# ---------------------------------------------------------------------------
# Detector module registry.
#
# Maps a short name to (module_file, class_name).  Most entries are
# SafetyDetector subclasses.  Two special components -- AlertPrioritizer
# and HazardFusionEngine -- have their own APIs and are handled separately
# in the dedicated endpoints.
# ---------------------------------------------------------------------------
DETECTOR_MODULES: dict[str, str] = {
    "wrong_way": "WrongWayDetector",
    "emergency_braking": "EmergencyBrakingDetector",
    "stalled_vehicle": "StalledVehicleDetector",
    "blind_intersection": "BlindIntersectionDetector",
    "blind_curve": "BlindCurveDetector",
    "emergency_vehicle": "EmergencyVehicleDetector",
    "animal_conflict": "AnimalConflictDetector",
    "road_hazard": "RoadHazardDetector",
}

# Non-detector components loaded alongside the detectors.
AUXILIARY_MODULES: dict[str, str] = {
    "alert_prioritization": "AlertPrioritizer",
    "hazard_fusion": "HazardFusionEngine",
}

# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class EvaluateRequest(BaseModel):
    world_state: dict[str, Any]


class EvaluateResponse(BaseModel):
    risks: list[RiskEvent]
    detector_count: int
    errors: list[str] = Field(default_factory=list)


class PrioritizeRequest(BaseModel):
    risks: list[RiskEvent]
    active_alerts: list[Alert] = Field(default_factory=list)
    actor_states: dict[str, Any] = Field(default_factory=dict)


class PrioritizeResponse(BaseModel):
    alerts: list[Alert]


class FuseRequest(BaseModel):
    observation: dict[str, Any]
    existing_hazards: list[dict[str, Any]] = Field(default_factory=list)


class FuseResponse(BaseModel):
    result: dict[str, Any]


class DetectorInfo(BaseModel):
    name: str
    risk_type: str
    version: str


class HealthResponse(BaseModel):
    status: str
    service: str
    version: str


# ---------------------------------------------------------------------------
# Application state (populated at startup)
# ---------------------------------------------------------------------------
_registry: SafetyPolicyRegistry | None = None
_detectors: dict[str, SafetyDetector] = {}
_alert_prioritizer: Any = None  # AlertPrioritizer instance (SafetyPolicy)
_hazard_fusion_engine: Any = None  # HazardFusionEngine instance
_config: PolicyConfig = PolicyConfig()


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
    logger.info("Starting Marga Safety Detectors Service")
    _init_all()
    yield


app = FastAPI(
    title="Marga Safety Detectors Service",
    version="0.1.0",
    description="Safety feature evaluation, alert generation, and hazard fusion for the Marga V2X platform.",
    lifespan=lifespan,
)


def _load_class(module_name: str, class_name: str) -> type | None:
    """Dynamically load a class from the detectors/ directory.

    Returns the class, or None if the module/class cannot be loaded.
    """
    module_path = _DETECTORS_DIR / f"{module_name}.py"
    if not module_path.exists():
        logger.warning("Module not found: %s", module_path)
        return None

    fq_module = f"detectors.{module_name}"
    spec = importlib.util.spec_from_file_location(fq_module, module_path)
    if spec is None or spec.loader is None:
        logger.warning("Cannot create module spec for %s", module_path)
        return None

    module = importlib.util.module_from_spec(spec)
    sys.modules[fq_module] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        logger.exception("Failed to load module: %s", module_name)
        return None

    cls = getattr(module, class_name, None)
    if cls is None:
        logger.warning("Class %s not found in module %s", class_name, module_name)
    return cls


def _init_all(config: PolicyConfig | None = None) -> None:
    """Instantiate all detectors, the alert prioritizer, and the fusion engine."""
    global _registry, _detectors, _alert_prioritizer, _hazard_fusion_engine, _config

    _config = config or PolicyConfig()
    _registry = SafetyPolicyRegistry(config=_config)
    _detectors = {}
    _alert_prioritizer = None
    _hazard_fusion_engine = None

    # -- Load SafetyDetector subclasses --
    for module_name, class_name in DETECTOR_MODULES.items():
        cls = _load_class(module_name, class_name)
        if cls is None:
            continue
        try:
            _registry.register_detector(module_name, cls)
            instance = _registry.create_detector(module_name)
            _detectors[module_name] = instance
            logger.info("Registered detector: %s (v%s)", instance.name, instance.version)
        except Exception:
            logger.exception("Failed to instantiate detector: %s", module_name)

    # -- Load AlertPrioritizer (SafetyPolicy, not SafetyDetector) --
    prioritizer_cls = _load_class("alert_prioritization", "AlertPrioritizer")
    if prioritizer_cls is not None:
        try:
            _alert_prioritizer = prioritizer_cls(config=_config)
            logger.info(
                "Registered alert prioritizer: %s (v%s)",
                _alert_prioritizer.name,
                _alert_prioritizer.version,
            )
        except Exception:
            logger.exception("Failed to instantiate AlertPrioritizer")

    # -- Load HazardFusionEngine (standalone engine, not SafetyDetector) --
    fusion_cls = _load_class("hazard_fusion", "HazardFusionEngine")
    if fusion_cls is not None:
        try:
            _hazard_fusion_engine = fusion_cls(config=_config)
            logger.info("Registered hazard fusion engine")
        except Exception:
            logger.exception("Failed to instantiate HazardFusionEngine")

    total = len(DETECTOR_MODULES) + len(AUXILIARY_MODULES)
    loaded = len(_detectors) + (1 if _alert_prioritizer else 0) + (1 if _hazard_fusion_engine else 0)
    logger.info("Initialization complete: %d/%d components loaded", loaded, total)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(
        status="healthy",
        service="safety-detectors",
        version="0.1.0",
    )


@app.post("/v1/evaluate", response_model=EvaluateResponse)
async def evaluate_all(request: EvaluateRequest) -> EvaluateResponse:
    """Run all registered SafetyDetector instances against the provided world state."""
    all_risks: list[RiskEvent] = []
    errors: list[str] = []

    for name, detector in _detectors.items():
        try:
            risks = detector.evaluate(request.world_state)
            all_risks.extend(risks)
        except Exception as exc:
            msg = f"Detector '{name}' failed: {exc}"
            logger.exception(msg)
            errors.append(msg)

    return EvaluateResponse(
        risks=all_risks,
        detector_count=len(_detectors),
        errors=errors,
    )


@app.post("/v1/evaluate/{detector_name}", response_model=EvaluateResponse)
async def evaluate_single(
    detector_name: str,
    request: EvaluateRequest,
) -> EvaluateResponse:
    """Run a single named detector against the provided world state."""
    detector = _detectors.get(detector_name)
    if detector is None:
        raise HTTPException(
            status_code=404,
            detail=f"Detector '{detector_name}' not found. "
            f"Available: {list(_detectors.keys())}",
        )

    errors: list[str] = []
    try:
        risks = detector.evaluate(request.world_state)
    except Exception as exc:
        msg = f"Detector '{detector_name}' failed: {exc}"
        logger.exception(msg)
        errors.append(msg)
        risks = []

    return EvaluateResponse(
        risks=risks,
        detector_count=1,
        errors=errors,
    )


@app.post("/v1/alerts/prioritize", response_model=PrioritizeResponse)
async def prioritize_alerts(request: PrioritizeRequest) -> PrioritizeResponse:
    """Prioritize risks into alerts using the AlertPrioritizer.

    Each RiskEvent is evaluated individually via ``evaluate_risk``.
    Suppression, hysteresis, and concurrent-alert limits are applied
    internally by the policy.
    """
    if _alert_prioritizer is None:
        raise HTTPException(
            status_code=503,
            detail="AlertPrioritizer is not loaded",
        )

    context: dict[str, Any] = {
        "active_alerts": request.active_alerts,
        "actor_states": request.actor_states,
    }

    alerts: list[Alert] = []
    try:
        for risk in request.risks:
            alert = _alert_prioritizer.evaluate_risk(risk, context)
            if alert is not None:
                alerts.append(alert)
    except Exception as exc:
        logger.exception("Alert prioritization failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return PrioritizeResponse(alerts=alerts)


@app.post("/v1/hazards/fuse", response_model=FuseResponse)
async def fuse_hazards(request: FuseRequest) -> FuseResponse:
    """Fuse a new hazard observation with existing hazards.

    The ``observation`` dict must contain:
    - hazard_type: HazardType value (e.g. "POTHOLE", "DEBRIS")
    - position: {"lat": float, "lon": float}
    - severity: float [0, 1]
    - confidence: float [0, 1]
    - source_id: str
    - ts: ISO-8601 datetime string
    - ttl_s: int (optional, default 3600)
    - is_negative: bool (optional, default false)
    """
    if _hazard_fusion_engine is None:
        raise HTTPException(
            status_code=503,
            detail="HazardFusionEngine is not loaded",
        )

    obs = request.observation
    try:
        from datetime import datetime as _dt
        from datetime import timezone as _tz

        from packages.schemas.canonical import HazardType as _HT

        ts_raw = obs.get("ts")
        if isinstance(ts_raw, str):
            ts_val = _dt.fromisoformat(ts_raw)
            if ts_val.tzinfo is None:
                ts_val = ts_val.replace(tzinfo=_tz.utc)
        else:
            ts_val = _dt.now(_tz.utc)

        hazard = _hazard_fusion_engine.process_observation(
            hazard_type=_HT(obs["hazard_type"]),
            position=obs["position"],
            severity=float(obs.get("severity", 0.5)),
            confidence=float(obs.get("confidence", 0.5)),
            source_id=str(obs["source_id"]),
            ts=ts_val,
            ttl_s=int(obs.get("ttl_s", 3600)),
            is_negative=bool(obs.get("is_negative", False)),
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=422,
            detail=f"Missing required observation field: {exc}",
        ) from exc
    except Exception as exc:
        logger.exception("Hazard fusion failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return FuseResponse(result={"hazard": hazard.model_dump(mode="json")})


@app.get("/v1/detectors", response_model=list[DetectorInfo])
async def list_detectors() -> list[DetectorInfo]:
    """List all registered detectors with name, risk type, and version."""
    infos: list[DetectorInfo] = []
    for detector in _detectors.values():
        infos.append(
            DetectorInfo(
                name=detector.name,
                risk_type=detector.risk_type.value,
                version=detector.version,
            )
        )
    # Include the prioritizer as a logical detector entry.
    if _alert_prioritizer is not None:
        infos.append(
            DetectorInfo(
                name=_alert_prioritizer.name,
                risk_type="ALERT_PRIORITIZATION",
                version=_alert_prioritizer.version,
            )
        )
    # Include the fusion engine.
    if _hazard_fusion_engine is not None:
        infos.append(
            DetectorInfo(
                name="hazard_fusion_engine",
                risk_type="HAZARD_FUSION",
                version="0.1.0",
            )
        )
    return infos


@app.get("/v1/config", response_model=PolicyConfig)
async def get_config() -> PolicyConfig:
    """Return the current PolicyConfig."""
    return _config


@app.put("/v1/config", response_model=PolicyConfig)
async def update_config(new_config: PolicyConfig) -> PolicyConfig:
    """Update the PolicyConfig for runtime tuning.

    This reinitializes all detectors with the new configuration.
    """
    logger.info("PolicyConfig updated to version %s, reinitializing all components", new_config.version)
    _init_all(new_config)
    return _config
