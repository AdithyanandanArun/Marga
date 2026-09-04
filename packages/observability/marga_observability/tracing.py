"""OpenTelemetry tracing setup for Marga services.

Call ``setup_tracing`` once at service startup::

    from marga_observability.tracing import setup_tracing, get_tracer

    setup_tracing("risk-engine", otlp_endpoint="http://otel-collector:4317")
    tracer = get_tracer("risk-engine")
    with tracer.start_as_current_span("evaluate_risk"):
        ...
"""

from __future__ import annotations

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter

_provider: TracerProvider | None = None


def setup_tracing(
    service_name: str,
    otlp_endpoint: str | None = None,
    *,
    console_export: bool = False,
) -> TracerProvider:
    """Initialize the global OTel tracer provider.

    Parameters
    ----------
    service_name:
        Logical name of the service (e.g. ``"risk-engine"``).
    otlp_endpoint:
        If provided, spans are exported to this OTLP gRPC endpoint.
        Example: ``"http://otel-collector:4317"``.
    console_export:
        If ``True`` (useful for local development), spans are printed to
        stderr via :class:`ConsoleSpanExporter`.
    """
    global _provider

    resource = Resource.create({"service.name": service_name})
    provider = TracerProvider(resource=resource)

    if otlp_endpoint is not None:
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
            OTLPSpanExporter,
        )

        exporter = OTLPSpanExporter(endpoint=otlp_endpoint, insecure=True)
        provider.add_span_processor(BatchSpanProcessor(exporter))

    if console_export:
        # Console spans are opt-in: test runners may close their capture stream
        # before the batch exporter flushes.
        provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))

    trace.set_tracer_provider(provider)
    _provider = provider
    return provider


def get_tracer(name: str) -> trace.Tracer:
    """Return a named tracer from the global provider."""
    return trace.get_tracer(name)
