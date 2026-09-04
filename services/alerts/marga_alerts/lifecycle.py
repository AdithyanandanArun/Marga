"""Alert lifecycle and hysteresis manager.

Maintains the alert state machine (ACTIVE → ACKNOWLEDGED → RESOLVED,
ACTIVE → EXPIRED, ACTIVE → SUPPRESSED) and prevents the same alert
type from firing repeatedly for the same set of actors within a
configurable cooldown window.
"""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from marga_schemas.alert import Alert, AlertState

# ---------------------------------------------------------------------------
# Valid state transitions
# ---------------------------------------------------------------------------
_TRANSITIONS: dict[AlertState, set[AlertState]] = {
    AlertState.ACTIVE: {AlertState.ACKNOWLEDGED, AlertState.RESOLVED, AlertState.EXPIRED, AlertState.SUPPRESSED},
    AlertState.ACKNOWLEDGED: {AlertState.RESOLVED},
    # Terminal states — no outgoing edges
    AlertState.RESOLVED: set(),
    AlertState.EXPIRED: set(),
    AlertState.SUPPRESSED: set(),
}


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass
class AlertLifecycleManager:
    """Manages creation, state transitions, and hysteresis of alerts."""

    # Cooldown (seconds) per alert_type before the same alert type
    # for the same set of actor IDs may fire again.
    default_cooldown_s: float = 10.0
    cooldowns: dict[str, float] = field(default_factory=dict)

    # Internal registries — not part of the public interface.
    _alerts: dict[UUID, Alert] = field(default_factory=dict)
    # key = (alert_type, frozenset(actor_ids)) → last-fire epoch
    _hysteresis: dict[tuple[str, frozenset[str]], float] = field(default_factory=lambda: defaultdict(float))
    # Counter for creation-rate metrics
    _created_count: int = field(default=0)

    # ---- public API -------------------------------------------------------

    def create_alert(self, alert: Alert) -> Alert:
        """Register a new alert after checking for hysteresis suppression.

        If a matching alert (same type + actors) was created within the
        cooldown window the alert is returned with state SUPPRESSED and
        is *not* stored as active.
        """
        key = self._hysteresis_key(alert)

        if self.is_suppressed(alert.alert_type, alert.affected_actor_ids):
            alert = alert.model_copy(update={"state": AlertState.SUPPRESSED})
            return alert

        now_epoch = time.monotonic()
        self._hysteresis[key] = now_epoch
        alert = alert.model_copy(update={"state": AlertState.ACTIVE})
        self._alerts[alert.alert_id] = alert
        self._created_count += 1
        return alert

    def update_alert(self, alert_id: UUID, updates: dict[str, Any]) -> Alert:
        """Apply *updates* to an existing alert, enforcing valid transitions.

        If ``state`` is present in *updates*, the transition is validated
        against the state machine.  Returns the updated alert.
        """
        alert = self._alerts.get(alert_id)
        if alert is None:
            raise KeyError(f"Alert {alert_id} not found")

        new_state = updates.get("state")
        if new_state is not None:
            if isinstance(new_state, str):
                new_state = AlertState(new_state)
            if new_state not in _TRANSITIONS.get(alert.state, set()):
                raise ValueError(f"Invalid transition: {alert.state.value} → {new_state.value}")
            updates["state"] = new_state

        updates["updated_at"] = _utcnow()
        alert = alert.model_copy(update=updates)
        self._alerts[alert.alert_id] = alert
        return alert

    def resolve_alert(self, alert_id: UUID, reason: str) -> Alert:
        """Transition an alert to RESOLVED with a resolution reason."""
        alert = self._alerts.get(alert_id)
        if alert is None:
            raise KeyError(f"Alert {alert_id} not found")

        if AlertState.RESOLVED not in _TRANSITIONS.get(alert.state, set()):
            raise ValueError(f"Cannot resolve alert in state {alert.state.value}")

        alert = alert.model_copy(
            update={
                "state": AlertState.RESOLVED,
                "updated_at": _utcnow(),
                "machine_reasoning": {**alert.machine_reasoning, "resolution_reason": reason},
            }
        )
        self._alerts[alert.alert_id] = alert
        return alert

    def expire_stale(self) -> list[UUID]:
        """Expire all active alerts whose TTL has passed.  Returns their IDs."""
        now = _utcnow()
        expired_ids: list[UUID] = []
        for alert in list(self._alerts.values()):
            if alert.state != AlertState.ACTIVE:
                continue
            if alert.expires_at is not None:
                exp = (
                    alert.expires_at.replace(tzinfo=UTC)
                    if alert.expires_at.tzinfo is None
                    else alert.expires_at
                )
                if now >= exp:
                    self._alerts[alert.alert_id] = alert.model_copy(
                        update={"state": AlertState.EXPIRED, "updated_at": now}
                    )
                    expired_ids.append(alert.alert_id)
            elif alert.ttl_s is not None:
                created = (
                    alert.created_at.replace(tzinfo=UTC)
                    if alert.created_at.tzinfo is None
                    else alert.created_at
                )
                if (now - created).total_seconds() >= alert.ttl_s:
                    self._alerts[alert.alert_id] = alert.model_copy(
                        update={"state": AlertState.EXPIRED, "updated_at": now}
                    )
                    expired_ids.append(alert.alert_id)
        return expired_ids

    def is_suppressed(self, alert_type: str, actor_ids: list[str]) -> bool:
        """Check whether a new alert of this type+actors is within cooldown."""
        key = self._hysteresis_key_raw(alert_type, frozenset(actor_ids))
        last_fire = self._hysteresis.get(key)
        if last_fire is None:
            return False
        cooldown = self.cooldowns.get(alert_type, self.default_cooldown_s)
        return (time.monotonic() - last_fire) < cooldown

    @property
    def created_count(self) -> int:
        """Total number of alerts successfully created (not suppressed)."""
        return self._created_count

    def get(self, alert_id: UUID) -> Alert | None:
        return self._alerts.get(alert_id)

    # ---- internal ---------------------------------------------------------

    @staticmethod
    def _hysteresis_key(alert: Alert) -> tuple[str, frozenset[str]]:
        return (alert.alert_type, frozenset(alert.affected_actor_ids))

    @staticmethod
    def _hysteresis_key_raw(alert_type: str, actor_ids: frozenset[str]) -> tuple[str, frozenset[str]]:
        return (alert_type, actor_ids)
