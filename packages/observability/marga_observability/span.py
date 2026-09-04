"""No-op-safe tracing spans — works whether or not OpenTelemetry is configured."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Generator

_available = False
try:
    from opentelemetry import trace

    _tracer_cache: dict[str, trace.Tracer] = {}
    _available = True
except ImportError:
    pass


@contextmanager
def optional_span(
    tracer_name: str,
    span_name: str,
    attributes: dict[str, Any] | None = None,
) -> Generator[Any, None, None]:
    """Create a tracing span if OTel is available, otherwise no-op."""
    if not _available:
        yield None
        return
    if tracer_name not in _tracer_cache:
        _tracer_cache[tracer_name] = trace.get_tracer(tracer_name)
    tracer = _tracer_cache[tracer_name]
    with tracer.start_as_current_span(span_name, attributes=attributes or {}) as span:
        yield span
