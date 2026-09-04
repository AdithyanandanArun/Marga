"""Health check utilities for Marga services.

Provides a uniform health-check model and helpers for common
dependencies (PostgreSQL, Redis).  Optionally creates a FastAPI
router that serves ``/healthz``.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine


class HealthStatus(str, enum.Enum):
    UP = "UP"
    DOWN = "DOWN"
    DEGRADED = "DEGRADED"


@dataclass
class HealthCheck:
    """Aggregated health report for a service."""

    service_name: str
    status: HealthStatus = HealthStatus.UP
    checks: dict[str, dict[str, Any]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "service": self.service_name,
            "status": self.status.value,
            "checks": self.checks,
        }


async def check_database(engine: AsyncEngine) -> bool:
    """Return ``True`` if the database responds to ``SELECT 1``."""
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


async def check_redis(url: str) -> bool:
    """Return ``True`` if Redis at *url* responds to PING.

    Requires the ``redis`` (``redis[hiredis]``) package to be installed.
    Returns ``False`` if the package is missing or the server is
    unreachable.
    """
    try:
        import redis.asyncio as aioredis

        client = aioredis.from_url(url, socket_connect_timeout=2)
        try:
            return await client.ping()
        finally:
            await client.aclose()
    except Exception:
        return False


def create_health_endpoint(
    service_name: str,
    checks: list[tuple[str, Any]],
) -> Any:
    """Create a FastAPI router with a ``/healthz`` endpoint.

    Parameters
    ----------
    service_name:
        Name of the service shown in the response body.
    checks:
        List of ``(name, check_coro_factory)`` pairs.  Each
        ``check_coro_factory`` is an async callable returning ``bool``.

    Returns
    -------
    fastapi.APIRouter
        Mount on the app with ``app.include_router(router)``.
    """
    from fastapi import APIRouter
    from fastapi.responses import JSONResponse

    router = APIRouter(tags=["health"])

    @router.get("/healthz")
    async def healthz() -> JSONResponse:
        report = HealthCheck(service_name=service_name)
        all_ok = True
        for name, check_fn in checks:
            try:
                ok = await check_fn()
            except Exception:
                ok = False
            report.checks[name] = {"status": "UP" if ok else "DOWN"}
            if not ok:
                all_ok = False

        report.status = HealthStatus.UP if all_ok else HealthStatus.DEGRADED
        status_code = 200 if all_ok else 503
        return JSONResponse(content=report.to_dict(), status_code=status_code)

    return router
