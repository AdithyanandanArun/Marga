"""Main FastAPI application — mounts all service routers, health/metrics endpoints, and middleware."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware

logger = logging.getLogger("marga.gateway")


async def _adaptive_signal_loop(interval_s: float) -> None:
    """Optional closed loop; it only applies actions when an adapter registers."""
    from services.gateway.signal_control import signal_controller

    while True:
        await asyncio.sleep(interval_s)
        for junction_id in signal_controller.junction_ids:
            try:
                if signal_controller.executor_registered:
                    signal_controller.decide_and_apply(junction_id)
                else:
                    signal_controller.recommend(junction_id)
            except Exception as exc:
                # Missing telemetry/topology is an observable transient state,
                # not a reason to manufacture a control action.
                logger.debug("adaptive signal tick skipped for %s: %s", junction_id, exc)


async def _stale_actor_reaper() -> None:
    """Evict actors whose producer stopped reporting.

    The TTL is the contract for "a live adapter refreshes its actors". It is
    generous relative to the 4 Hz browser adapter, so a slow frame or a brief
    gateway hiccup never blinks a real vehicle off the map.
    """
    from services.gateway.world_state import DEFAULT_ACTOR_TTL_S, sweep_stale_entities

    ttl_s = max(2.0, float(os.environ.get("MARGA_ACTOR_TTL_S", DEFAULT_ACTOR_TTL_S)))
    interval_s = max(1.0, ttl_s / 4)
    while True:
        await asyncio.sleep(interval_s)
        try:
            await sweep_stale_entities(ttl_s)
        except Exception as exc:
            logger.debug("stale actor sweep skipped: %s", exc)


# ---------------------------------------------------------------------------
# Optional OpenTelemetry instrumentation — only activates when the exporter
# environment variables are configured.
# ---------------------------------------------------------------------------

_otel_available = False
try:
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

    _otel_available = True
except ImportError:
    pass

# ---------------------------------------------------------------------------
# Optional Prometheus metrics
# ---------------------------------------------------------------------------

_prometheus_available = False
try:
    from prometheus_client import (
        CONTENT_TYPE_LATEST,
        Counter,
        Histogram,
        generate_latest,
    )

    _prometheus_available = True
    _request_count = Counter(
        "marga_http_requests_total",
        "Total HTTP requests",
        ["method", "path", "status"],
    )
    _request_duration = Histogram(
        "marga_http_request_duration_seconds",
        "HTTP request duration in seconds",
        ["method", "path"],
    )
except ImportError:
    pass


# ---------------------------------------------------------------------------
# Lifespan — startup / shutdown hooks
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    logger.info("Marga gateway starting up")

    # -- OpenTelemetry tracing (obj 2.4) --
    try:
        from marga_observability.tracing import setup_tracing

        _otlp = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
        _svc = os.environ.get("OTEL_SERVICE_NAME", "marga-gateway")
        setup_tracing(_svc, otlp_endpoint=_otlp)
        logger.info("OpenTelemetry tracing configured (endpoint=%s)", _otlp)
    except Exception as exc:
        logger.debug("OpenTelemetry tracing not configured: %s", exc)

    # -- NATS JetStream event bus (obj 2.1) --
    try:
        from packages.event_bus.bus import EventBus, set_event_bus

        _nats_url = os.environ.get("EVENT_BUS_URL")
        if _nats_url:
            _bus = EventBus(_nats_url)
            await _bus.connect()
            set_event_bus(_bus)
    except Exception as exc:
        logger.debug("Event bus not available: %s", exc)

    # -- Redis actor TTL + dedup (obj 2.2) --
    try:
        from packages.redis_store.actor_ttl import ActorTTLManager, set_ttl_manager

        _redis_url = os.environ.get("REDIS_URL")
        if _redis_url:
            _ttl_mgr = ActorTTLManager(_redis_url)
            await _ttl_mgr.connect()
            set_ttl_manager(_ttl_mgr)
    except Exception as exc:
        logger.debug("Redis actor TTL not available: %s", exc)

    # Mounted applications do not receive an independent lifespan under every
    # ASGI server, so initialize the safety registry explicitly here.
    initialize_safety_detectors()
    try:
        from marga_routing.api import initialize as initialize_routing

        from services.gateway.v2x_bridge import initialize as initialize_v2x

        initialize_routing()
        await initialize_v2x()
    except ImportError as exc:
        logger.warning("routing or edge V2X integration unavailable: %s", exc)
    # Abandoned producers (a closed or reloaded browser tab) never retire their
    # actors, so without this sweep the world accumulates frozen vehicles that
    # are indistinguishable from live ones on the map.
    reaper_task = asyncio.create_task(_stale_actor_reaper())
    signal_task: asyncio.Task[None] | None = None
    if os.environ.get("MARGA_SIGNAL_CONTROL_ENABLED", "false").lower() == "true":
        interval_s = max(1.0, float(os.environ.get("MARGA_SIGNAL_CONTROL_INTERVAL_S", "5")))
        signal_task = asyncio.create_task(_adaptive_signal_loop(interval_s))
        logger.info("adaptive signal control loop enabled at %.1fs", interval_s)
    yield

    for task in (reaper_task, signal_task):
        if task is None:
            continue
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    try:
        from services.gateway.v2x_bridge import shutdown as shutdown_v2x

        await shutdown_v2x()
    except ImportError:
        pass

    # -- Shutdown --
    try:
        from packages.event_bus.bus import get_event_bus

        bus = get_event_bus()
        if bus:
            await bus.close()
    except Exception:
        pass
    try:
        from packages.redis_store.actor_ttl import get_ttl_manager

        mgr = get_ttl_manager()
        if mgr:
            await mgr.close()
    except Exception:
        pass
    logger.info("Marga gateway shutting down")


# ---------------------------------------------------------------------------
# Application factory
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Marga V2X Gateway",
    description="India-ready V2X resilience platform API gateway",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS — permissive in dev, tighten via environment for production.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# OpenTelemetry auto-instrumentation
if _otel_available:
    FastAPIInstrumentor.instrument_app(app)


# ---------------------------------------------------------------------------
# Gateway-owned canonical contracts must register before optional downstream
# routers. Otherwise the hazard service's similarly named endpoint shadows the
# canonical gateway ingestion route.
from services.gateway.replay import router as _replay_router  # noqa: E402
from services.gateway.world_state import router as _world_state_router  # noqa: E402

app.include_router(_world_state_router)
app.include_router(_replay_router)


# Service router mounting — guarded imports so the gateway starts even when
# downstream service packages are not yet implemented.
# ---------------------------------------------------------------------------


def _try_mount_router(module_path: str, attr: str, prefix: str, tag: str) -> None:
    """Attempt to import and mount a service router; log and skip on failure."""
    try:
        import importlib

        mod = importlib.import_module(module_path)
        router = getattr(mod, attr)
        app.include_router(router, prefix=prefix, tags=[tag])
        logger.info("Mounted router %s at %s", module_path, prefix)
    except (ImportError, AttributeError) as exc:
        logger.debug("Router %s not available: %s", module_path, exc)


_try_mount_router("services.hazards.marga_hazards.api", "router", "", "hazards")
_try_mount_router("services.trust.marga_trust.api", "router", "", "trust")
_try_mount_router("services.messaging.marga_messaging.api", "router", "", "messaging")
_try_mount_router("services.alerts.marga_alerts.api", "router", "", "alerts")
_try_mount_router("services.mobility_graph.api", "router", "", "mobility-graph")
_try_mount_router("services.gateway.signal_control", "router", "", "signal-control")
_try_mount_router("services.gateway.v2x_bridge", "router", "", "edge-v2x")

try:
    from marga_routing.api import app as routing_app

    app.include_router(routing_app.router, tags=["routing"])
    logger.info("Mounted cooperative routing routes")
except ImportError as exc:
    logger.warning("Routing service unavailable: %s", exc)

# Hrishi's safety service is a FastAPI application (rather than an APIRouter),
# so mount it at an explicit namespace.  This leaves existing public gateway
# routes stable while giving every deployment one production import path.
try:
    from services.safety_detectors import app as safety_app
    from services.safety_detectors import initialize as initialize_safety_detectors

    app.mount("/safety", safety_app)
    logger.info("Mounted safety-detectors service at /safety")
except ImportError as exc:
    logger.warning("Safety-detectors service unavailable: %s", exc)

    def initialize_safety_detectors() -> None:
        return None


# ---------------------------------------------------------------------------
# Core endpoints
# ---------------------------------------------------------------------------

_STARTUP_TIME = time.time()


@app.get("/health", tags=["ops"])
async def health() -> dict[str, Any]:
    """Aggregate health check endpoint."""
    return {
        "status": "ok",
        "service": "marga-gateway",
        "version": "0.1.0",
        "uptime_s": round(time.time() - _STARTUP_TIME, 1),
    }


@app.get("/metrics", tags=["ops"], include_in_schema=False)
async def metrics_endpoint() -> Response:
    """Prometheus-compatible metrics endpoint — exposes all service metrics."""
    if not _prometheus_available:
        return Response(content="# prometheus-client not installed\n", media_type="text/plain")
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
