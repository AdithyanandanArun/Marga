"""Network fault model for test and simulation — latency, loss, partitions, bandwidth."""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from marga_schemas.common import ConnectivityState
from marga_schemas.messaging import LinkState, V2XMessage

from .transport import V2XTransport

logger = logging.getLogger(__name__)


@dataclass
class LinkConfig:
    """Configuration for a single network link's fault characteristics."""

    latency_mean_ms: float = 0.0
    latency_stddev_ms: float = 0.0
    packet_loss_probability: float = 0.0
    bandwidth_bytes_per_sec: float = float("inf")
    # Token bucket: burst capacity in bytes.
    burst_capacity_bytes: float = float("inf")


@dataclass
class NetworkModel:
    """Configurable network fault model.

    Supports per-link latency distributions, packet loss, bandwidth/token bucket
    constraints, partition rules, range limits, and independent cloud vs direct
    route availability.
    """

    # Default link configuration applied to all messages unless overridden.
    default_link: LinkConfig = field(default_factory=LinkConfig)

    # Per-topic link overrides: topic pattern -> LinkConfig.
    topic_overrides: dict[str, LinkConfig] = field(default_factory=dict)

    # Partition rules: set of (source_node, dest_node) pairs that are partitioned.
    partitioned_pairs: set[tuple[str, str]] = field(default_factory=set)

    # Partition rules by region: set of region IDs that are isolated.
    partitioned_regions: set[str] = field(default_factory=set)

    # Node-to-region mapping.
    node_regions: dict[str, str] = field(default_factory=dict)

    # Direct range limit in meters (None = unlimited).
    direct_range_m: float | None = None

    # Cloud route availability (independent from direct route).
    cloud_available: bool = True

    # Direct route availability.
    direct_available: bool = True

    def get_link_config(self, topic: str) -> LinkConfig:
        """Return the link config for a given topic, falling back to default."""
        return self.topic_overrides.get(topic, self.default_link)

    def is_partitioned(self, source_node: str, dest_node: str) -> bool:
        """Check if two nodes are partitioned from each other."""
        if (source_node, dest_node) in self.partitioned_pairs:
            return True
        if (dest_node, source_node) in self.partitioned_pairs:
            return True

        src_region = self.node_regions.get(source_node)
        dst_region = self.node_regions.get(dest_node)
        if src_region and src_region in self.partitioned_regions:
            return True
        if dst_region and dst_region in self.partitioned_regions:
            return True

        return False


class _TokenBucket:
    """Simple token bucket for bandwidth limiting."""

    def __init__(self, rate: float, capacity: float) -> None:
        self._rate = rate  # tokens (bytes) per second
        self._capacity = capacity
        self._tokens = capacity
        self._last_refill = time.monotonic()

    def consume(self, tokens: float) -> bool:
        """Try to consume tokens. Returns True if allowed."""
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)
        self._last_refill = now

        if self._tokens >= tokens:
            self._tokens -= tokens
            return True
        return False


class NetworkModelDecorator:
    """Wraps a V2XTransport and applies network fault model effects.

    This decorator actually delays and drops messages — it is not a flag-based
    simulation but applies real asyncio delays and probabilistic drops.
    """

    def __init__(
        self,
        transport: V2XTransport,
        model: NetworkModel,
        *,
        source_node: str = "local",
    ) -> None:
        self._transport = transport
        self._model = model
        self._source_node = source_node
        self._bucket: _TokenBucket | None = None
        self._init_bucket()

    def _init_bucket(self) -> None:
        link = self._model.default_link
        if link.bandwidth_bytes_per_sec < float("inf"):
            self._bucket = _TokenBucket(
                rate=link.bandwidth_bytes_per_sec,
                capacity=link.burst_capacity_bytes
                if link.burst_capacity_bytes < float("inf")
                else link.bandwidth_bytes_per_sec * 2,
            )

    async def publish(self, topic: str, message: V2XMessage, qos: int = 0) -> bool:
        """Publish with simulated network effects: latency, loss, partitions, bandwidth."""
        link = self._model.get_link_config(topic)

        # Check cloud availability for publish (assuming publish goes via cloud).
        if not self._model.cloud_available and not self._model.direct_available:
            logger.debug("Both cloud and direct routes unavailable, dropping message")
            return False

        # Check partition rules.
        # For publish, we check if the sender is in a partitioned region/pair.
        if self._model.node_regions.get(self._source_node) in self._model.partitioned_regions:
            logger.debug("Source node %s is in partitioned region", self._source_node)
            return False

        # Simulate packet loss.
        if link.packet_loss_probability > 0 and random.random() < link.packet_loss_probability:
            logger.debug("Simulated packet loss for message %s", message.message_id)
            return False

        # Bandwidth / token bucket.
        if self._bucket is not None:
            # Estimate message size (rough approximation).
            msg_size = len(message.model_dump_json().encode())
            if not self._bucket.consume(msg_size):
                logger.debug("Bandwidth limit exceeded, dropping message %s", message.message_id)
                return False

        # Simulate latency.
        if link.latency_mean_ms > 0:
            latency = max(0, random.gauss(link.latency_mean_ms, link.latency_stddev_ms))
            await asyncio.sleep(latency / 1000.0)

        return await self._transport.publish(topic, message, qos)

    async def subscribe(self, topic_pattern: str, handler: Callable) -> str:
        """Subscribe with optional latency applied to incoming messages."""
        link = self._model.get_link_config(topic_pattern)

        if link.latency_mean_ms > 0 or link.packet_loss_probability > 0:
            # Wrap the handler to apply network effects on receive side.
            original_handler = handler

            async def delayed_handler(topic: str, msg: V2XMessage) -> None:
                # Simulate receive-side packet loss.
                if link.packet_loss_probability > 0 and random.random() < link.packet_loss_probability:
                    return

                # Simulate receive-side latency.
                if link.latency_mean_ms > 0:
                    latency = max(0, random.gauss(link.latency_mean_ms, link.latency_stddev_ms))
                    await asyncio.sleep(latency / 1000.0)

                await original_handler(topic, msg)

            handler = delayed_handler

        return await self._transport.subscribe(topic_pattern, handler)

    async def unsubscribe(self, subscription_id: str) -> None:
        await self._transport.unsubscribe(subscription_id)

    def get_link_state(self) -> LinkState:
        state = self._transport.get_link_state()
        # Override connectivity based on model.
        if not self._model.cloud_available and not self._model.direct_available:
            state.connectivity = ConnectivityState.ISOLATED
        elif not self._model.cloud_available:
            state.connectivity = ConnectivityState.DIRECT_ONLY
        return state

    async def close(self) -> None:
        await self._transport.close()
