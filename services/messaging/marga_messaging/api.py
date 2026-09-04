"""FastAPI router for V2X messaging service."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException
from marga_schemas.common import ConnectivityState
from marga_schemas.messaging import (
    LinkState,
    V2XMessage,
)
from pydantic import BaseModel, Field

from .connectivity import ConnectivityMonitor
from .priority import MessagePriorityQueue
from .store_forward import StoreForwardManager
from .transport import V2XTransport

router = APIRouter(prefix="/v1/messaging", tags=["messaging"])


# --- Request / Response models ---


class PublishRequest(BaseModel):
    topic: str
    message: V2XMessage
    qos: int = Field(default=0, ge=0, le=2)


class PublishResponse(BaseModel):
    accepted: bool
    message_id: str


class FlushRequest(BaseModel):
    connectivity: ConnectivityState = ConnectivityState.FULL


class FlushResponse(BaseModel):
    forwarded_count: int
    messages: list[dict[str, Any]]


class HealthResponse(BaseModel):
    status: str
    connectivity: ConnectivityState
    timestamp: str


# --- Service singleton wiring (lightweight DI) ---
# In production this would use FastAPI's Depends() with proper lifecycle management.
# For now, we expose module-level factories that the application entrypoint can override.

_transport: V2XTransport | None = None
_priority_queue: MessagePriorityQueue | None = None
_store_forward: StoreForwardManager | None = None
_connectivity: ConnectivityMonitor | None = None


def configure(
    transport: V2XTransport | None = None,
    priority_queue: MessagePriorityQueue | None = None,
    store_forward: StoreForwardManager | None = None,
    connectivity_monitor: ConnectivityMonitor | None = None,
) -> None:
    """Configure the messaging service components. Call before starting the app."""
    global _transport, _priority_queue, _store_forward, _connectivity
    _transport = transport
    _priority_queue = priority_queue
    _store_forward = store_forward
    _connectivity = connectivity_monitor


def _get_transport() -> V2XTransport:
    if _transport is None:
        raise HTTPException(status_code=503, detail="Transport not configured")
    return _transport


def _get_priority_queue() -> MessagePriorityQueue:
    if _priority_queue is None:
        raise HTTPException(status_code=503, detail="Priority queue not configured")
    return _priority_queue


def _get_store_forward() -> StoreForwardManager:
    if _store_forward is None:
        raise HTTPException(status_code=503, detail="Store-forward not configured")
    return _store_forward


def _get_connectivity() -> ConnectivityMonitor:
    if _connectivity is None:
        raise HTTPException(status_code=503, detail="Connectivity monitor not configured")
    return _connectivity


# --- Endpoints ---


@router.post("/publish", response_model=PublishResponse)
async def publish_message(request: PublishRequest) -> PublishResponse:
    """Publish a V2X message through the transport layer.

    The message is also enqueued in the priority queue for congestion control.
    """
    transport = _get_transport()
    pq = _get_priority_queue()

    # Enqueue for priority management.
    accepted_by_queue = pq.enqueue(request.message)
    if not accepted_by_queue:
        return PublishResponse(
            accepted=False,
            message_id=str(request.message.message_id),
        )

    # Publish through transport.
    accepted = await transport.publish(request.topic, request.message, request.qos)

    return PublishResponse(
        accepted=accepted,
        message_id=str(request.message.message_id),
    )


@router.get("/link-state", response_model=LinkState)
async def get_link_state() -> LinkState:
    """Return the current V2X link state."""
    connectivity = _get_connectivity()
    link_state = connectivity.get_link_state()

    # Augment with queue depth info.
    try:
        pq = _get_priority_queue()
        link_state.queue_depth = {
            k: v for k, v in pq.get_stats().items() if k not in ("total_enqueued", "total_dropped")
        }
    except HTTPException:
        pass

    return link_state


@router.get("/queue-stats")
async def get_queue_stats() -> dict[str, int]:
    """Return priority queue statistics."""
    pq = _get_priority_queue()
    return pq.get_stats()


@router.post("/store-forward/flush", response_model=FlushResponse)
async def flush_store_forward(request: FlushRequest) -> FlushResponse:
    """Trigger store-and-forward flush given the current connectivity state."""
    sf = _get_store_forward()
    messages = sf.flush(request.connectivity)

    return FlushResponse(
        forwarded_count=len(messages),
        messages=[{"message_id": str(m.message_id), "topic": m.topic, "priority": m.priority.value} for m in messages],
    )


@router.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """Health check endpoint."""
    try:
        connectivity = _get_connectivity()
        state = connectivity.get_state()
    except HTTPException:
        state = ConnectivityState.ISOLATED

    return HealthResponse(
        status="ok",
        connectivity=state,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )
