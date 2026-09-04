"""Prometheus-compatible metrics for all Marga services.

Import the singleton ``metrics`` object to instrument code::

    from marga_observability.metrics import metrics

    metrics.actor_updates_total.labels(source="SIMULATION").inc()
    metrics.risk_latency_seconds.observe(0.042)
"""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram


class MargaMetrics:
    """Centralized metric definitions — one instance shared across a process."""

    def __init__(self) -> None:
        # --- Actor / ingestion ---
        self.actor_updates_total = Counter(
            "marga_actor_updates_total",
            "Total actor state updates ingested",
            labelnames=["source"],
        )

        # --- Event bus ---
        self.event_bus_lag_seconds = Histogram(
            "marga_event_bus_lag_seconds",
            "Lag between event production and consumption",
        )

        # --- Risk engine ---
        self.risk_evaluations_total = Counter(
            "marga_risk_evaluations_total",
            "Total risk evaluation cycles executed",
        )
        self.risk_latency_seconds = Histogram(
            "marga_risk_latency_seconds",
            "Latency of individual risk evaluations",
            buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0),
        )

        # --- Alerts ---
        self.alerts_issued_total = Counter(
            "marga_alerts_issued_total",
            "Total alerts issued",
            labelnames=["alert_type", "priority"],
        )
        self.alerts_cleared_total = Counter(
            "marga_alerts_cleared_total",
            "Total alerts cleared or resolved",
        )

        # --- Position ---
        self.position_uncertainty_meters = Histogram(
            "marga_position_uncertainty_meters",
            "Distribution of position uncertainty radii",
        )

        # --- Transport reliability ---
        self.dropped_messages_total = Counter(
            "marga_dropped_messages_total",
            "Messages dropped due to errors or policy",
            labelnames=["reason"],
        )

        # --- Trust ---
        self.trust_rejections_total = Counter(
            "marga_trust_rejections_total",
            "Messages rejected by the trust layer",
            labelnames=["reason"],
        )

        # --- WebSocket ---
        self.websocket_clients = Gauge(
            "marga_websocket_clients",
            "Currently connected WebSocket clients",
        )
        self.websocket_bytes_sent_total = Counter(
            "marga_websocket_bytes_sent_total",
            "Total bytes sent over WebSocket connections",
        )

        # --- Hazard fusion ---
        self.hazard_fusion_operations_total = Counter(
            "marga_hazard_fusion_operations_total",
            "Total hazard fusion operations",
            labelnames=["result"],
        )

        # --- Store-and-forward ---
        self.store_forward_queue_depth = Gauge(
            "marga_store_forward_queue_depth",
            "Current depth of store-and-forward queues",
            labelnames=["queue_class"],
        )


# Module-level singleton — import this from anywhere.
metrics = MargaMetrics()
