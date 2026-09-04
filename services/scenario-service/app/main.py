"""
FastAPI application for the Marga Scenario Service.

Provides REST endpoints for:
  - Scenario CRUD and import/export
  - Scenario run lifecycle (start, pause, resume, stop, speed)
  - Live failure inspection and one-off failure injection

All endpoints are versioned under /v1/.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse

from .config import Settings, get_settings
from .failure_injector import FailureInjector
from .scenario_store import RunNotFoundError, ScenarioNotFoundError, ScenarioStore
from .schemas import (
    FailureScheduleEntry,
    InjectFailureRequest,
    ScenarioDefinition,
    ScenarioRun,
    ScenarioRunState,
    SpeedRequest,
)
from .time_control import TimeController

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Application state
# ---------------------------------------------------------------------------

class AppState:
    """Holds singletons that live for the lifetime of the process."""

    store: ScenarioStore
    injector: FailureInjector
    # Map run_id -> TimeController for runs that are currently managed
    # in-process.  In a production deployment this would be offloaded to a
    # dedicated simulation worker, but for the hackathon we keep it simple.
    controllers: dict[str, TimeController]

    def __init__(self) -> None:
        self.controllers = {}


_app_state = AppState()


def get_store(settings: Settings = Depends(get_settings)) -> ScenarioStore:
    return _app_state.store


def get_injector() -> FailureInjector:
    return _app_state.injector


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    _app_state.store = ScenarioStore(settings.DATA_DIR)
    _app_state.injector = FailureInjector()
    logger.info(
        "Scenario service started; data dir=%s", settings.DATA_DIR
    )
    yield
    logger.info("Scenario service shutting down")


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Marga Scenario Service",
    version="0.1.0",
    description=(
        "Deterministic scenario execution, failure injection, and time control "
        "for the Marga V2X safety platform."
    ),
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@app.get("/health", tags=["meta"])
async def health(settings: Settings = Depends(get_settings)) -> dict:
    return {"status": "ok", "service": settings.SERVICE_NAME}


# ---------------------------------------------------------------------------
# Scenario endpoints
# ---------------------------------------------------------------------------

@app.get("/v1/scenarios", response_model=list[ScenarioDefinition], tags=["scenarios"])
async def list_scenarios(
    tag: Optional[str] = Query(None, description="Filter by tag"),
    store: ScenarioStore = Depends(get_store),
) -> list[ScenarioDefinition]:
    """List all scenarios, with optional tag filter."""
    return await store.list_scenarios(tag=tag)


@app.post(
    "/v1/scenarios",
    response_model=ScenarioDefinition,
    status_code=201,
    tags=["scenarios"],
)
async def create_scenario(
    body: ScenarioDefinition,
    store: ScenarioStore = Depends(get_store),
) -> ScenarioDefinition:
    """Create and persist a new scenario definition."""
    await store.save_scenario(body)
    return body


@app.get(
    "/v1/scenarios/{scenario_id}",
    response_model=ScenarioDefinition,
    tags=["scenarios"],
)
async def get_scenario(
    scenario_id: str,
    store: ScenarioStore = Depends(get_store),
) -> ScenarioDefinition:
    scenario = await store.get_scenario(scenario_id)
    if scenario is None:
        raise HTTPException(status_code=404, detail=f"Scenario {scenario_id!r} not found")
    return scenario


@app.delete("/v1/scenarios/{scenario_id}", status_code=204, tags=["scenarios"])
async def delete_scenario(
    scenario_id: str,
    store: ScenarioStore = Depends(get_store),
) -> None:
    deleted = await store.delete_scenario(scenario_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Scenario {scenario_id!r} not found")


@app.get(
    "/v1/scenarios/{scenario_id}/export",
    tags=["scenarios"],
)
async def export_scenario(
    scenario_id: str,
    store: ScenarioStore = Depends(get_store),
) -> JSONResponse:
    """Export a scenario definition as JSON."""
    try:
        data = await store.export_scenario(scenario_id)
    except ScenarioNotFoundError:
        raise HTTPException(status_code=404, detail=f"Scenario {scenario_id!r} not found")
    return JSONResponse(content=data)


@app.post(
    "/v1/scenarios/import",
    response_model=ScenarioDefinition,
    status_code=201,
    tags=["scenarios"],
)
async def import_scenario(
    body: dict,
    store: ScenarioStore = Depends(get_store),
) -> ScenarioDefinition:
    """Import a scenario from a JSON payload."""
    try:
        scenario = await store.import_scenario(body)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return scenario


# ---------------------------------------------------------------------------
# Run endpoints
# ---------------------------------------------------------------------------

@app.post(
    "/v1/scenarios/{scenario_id}/runs",
    response_model=ScenarioRun,
    status_code=201,
    tags=["runs"],
)
async def start_run(
    scenario_id: str,
    store: ScenarioStore = Depends(get_store),
) -> ScenarioRun:
    """Start a new run for the given scenario."""
    scenario = await store.get_scenario(scenario_id)
    if scenario is None:
        raise HTTPException(status_code=404, detail=f"Scenario {scenario_id!r} not found")

    run = ScenarioRun(
        scenario_id=scenario_id,
        state=ScenarioRunState.running,
        started_at=datetime.now(timezone.utc),
    )
    await store.save_run(run)

    # Initialise an in-process time controller for this run.
    controller = TimeController()
    controller.start()
    _app_state.controllers[run.run_id] = controller

    logger.info("Started run %s for scenario %s", run.run_id, scenario_id)
    return run


@app.get("/v1/runs", response_model=list[ScenarioRun], tags=["runs"])
async def list_runs(
    scenario_id: Optional[str] = Query(None),
    store: ScenarioStore = Depends(get_store),
) -> list[ScenarioRun]:
    return await store.list_runs(scenario_id=scenario_id)


@app.get("/v1/runs/{run_id}", response_model=ScenarioRun, tags=["runs"])
async def get_run(
    run_id: str,
    store: ScenarioStore = Depends(get_store),
) -> ScenarioRun:
    """Get the current state of a run, with live sim time if running."""
    run = await store.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"Run {run_id!r} not found")

    controller = _app_state.controllers.get(run_id)
    if controller is not None:
        run = run.model_copy(update={"current_sim_time_s": controller.sim_time_s})

    return run


async def _require_run(run_id: str, store: ScenarioStore) -> ScenarioRun:
    run = await store.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"Run {run_id!r} not found")
    return run


@app.post("/v1/runs/{run_id}/pause", response_model=ScenarioRun, tags=["runs"])
async def pause_run(
    run_id: str,
    store: ScenarioStore = Depends(get_store),
) -> ScenarioRun:
    run = await _require_run(run_id, store)
    if run.state != ScenarioRunState.running:
        raise HTTPException(
            status_code=409,
            detail=f"Run is {run.state.value}, cannot pause",
        )
    controller = _app_state.controllers.get(run_id)
    if controller:
        controller.pause()
    run = run.model_copy(
        update={
            "state": ScenarioRunState.paused,
            "current_sim_time_s": controller.sim_time_s if controller else run.current_sim_time_s,
        }
    )
    await store.update_run(run)
    return run


@app.post("/v1/runs/{run_id}/resume", response_model=ScenarioRun, tags=["runs"])
async def resume_run(
    run_id: str,
    store: ScenarioStore = Depends(get_store),
) -> ScenarioRun:
    run = await _require_run(run_id, store)
    if run.state != ScenarioRunState.paused:
        raise HTTPException(
            status_code=409,
            detail=f"Run is {run.state.value}, cannot resume",
        )
    controller = _app_state.controllers.get(run_id)
    if controller:
        controller.resume()
    run = run.model_copy(update={"state": ScenarioRunState.running})
    await store.update_run(run)
    return run


@app.post("/v1/runs/{run_id}/stop", response_model=ScenarioRun, tags=["runs"])
async def stop_run(
    run_id: str,
    store: ScenarioStore = Depends(get_store),
) -> ScenarioRun:
    run = await _require_run(run_id, store)
    if run.state in (ScenarioRunState.completed, ScenarioRunState.cancelled):
        raise HTTPException(
            status_code=409,
            detail=f"Run is already {run.state.value}",
        )
    controller = _app_state.controllers.pop(run_id, None)
    if controller:
        controller.pause()
        final_sim_time = controller.sim_time_s
    else:
        final_sim_time = run.current_sim_time_s

    run = run.model_copy(
        update={
            "state": ScenarioRunState.cancelled,
            "completed_at": datetime.now(timezone.utc),
            "current_sim_time_s": final_sim_time,
        }
    )
    await store.update_run(run)
    return run


@app.put("/v1/runs/{run_id}/speed", response_model=ScenarioRun, tags=["runs"])
async def set_run_speed(
    run_id: str,
    body: SpeedRequest,
    store: ScenarioStore = Depends(get_store),
) -> ScenarioRun:
    """Set the simulation speed multiplier for a run (0.1 – 10.0)."""
    run = await _require_run(run_id, store)
    if run.state not in (ScenarioRunState.running, ScenarioRunState.paused):
        raise HTTPException(
            status_code=409,
            detail=f"Run is {run.state.value}, cannot change speed",
        )
    controller = _app_state.controllers.get(run_id)
    if controller:
        controller.set_speed(body.multiplier)
    run = run.model_copy(update={"speed_multiplier": body.multiplier})
    await store.update_run(run)
    return run


@app.get(
    "/v1/runs/{run_id}/failures",
    tags=["failures"],
)
async def list_active_failures(
    run_id: str,
    store: ScenarioStore = Depends(get_store),
    injector: FailureInjector = Depends(get_injector),
) -> dict:
    """List the failure effects that are currently active for this run."""
    run = await _require_run(run_id, store)
    scenario = await store.get_scenario(run.scenario_id)
    if scenario is None:
        raise HTTPException(
            status_code=404,
            detail=f"Scenario {run.scenario_id!r} not found",
        )

    controller = _app_state.controllers.get(run_id)
    sim_time = controller.sim_time_s if controller else run.current_sim_time_s

    effects = injector.get_active_failures(scenario.failure_schedule, sim_time)
    return {
        "run_id": run_id,
        "sim_time_s": sim_time,
        "active_failures": [e.model_dump() for e in effects],
    }


@app.post(
    "/v1/runs/{run_id}/inject",
    status_code=201,
    tags=["failures"],
)
async def inject_failure(
    run_id: str,
    body: InjectFailureRequest,
    store: ScenarioStore = Depends(get_store),
) -> dict:
    """
    Inject a one-off failure into a running scenario.

    The failure starts immediately (at the current sim time) and is appended
    to the scenario's failure schedule so that it is visible to all consumers
    that call get_active_failures.
    """
    run = await _require_run(run_id, store)
    if run.state not in (ScenarioRunState.running, ScenarioRunState.paused):
        raise HTTPException(
            status_code=409,
            detail=f"Run is {run.state.value}, cannot inject failure",
        )

    scenario = await store.get_scenario(run.scenario_id)
    if scenario is None:
        raise HTTPException(status_code=404, detail="Scenario not found")

    controller = _app_state.controllers.get(run_id)
    start_time = controller.sim_time_s if controller else run.current_sim_time_s

    entry = FailureScheduleEntry(
        failure_type=body.failure_type,
        start_sim_time_s=start_time,
        duration_s=body.duration_s,
        parameters=body.parameters,
    )

    updated_schedule = scenario.failure_schedule + [entry]
    scenario = scenario.model_copy(update={"failure_schedule": updated_schedule})
    await store.save_scenario(scenario)

    logger.info(
        "Injected %s failure (entry %s) into run %s at t=%.1fs",
        body.failure_type,
        entry.entry_id,
        run_id,
        start_time,
    )
    return {
        "run_id": run_id,
        "entry_id": entry.entry_id,
        "failure_type": body.failure_type,
        "start_sim_time_s": start_time,
        "duration_s": body.duration_s,
    }
