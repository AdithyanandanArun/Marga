"""Alert prioritization engine per Section 30.

Converts RiskEvents into prioritized, human-facing Alerts with full
lifecycle management, hysteresis, and suppression logic. Key principles:

- Critical collision alerts pre-empt informational road hazards.
- Alerts are not repeated every telemetry tick -- lifecycle and hysteresis
  prevent alert fatigue.
- Alerts are cleared or downgraded when the underlying risk resolves.
- Driver-facing text does not claim certainty beyond the evidence.
- Machine reasoning is stored separately from concise driver-facing text.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from packages.safety_policies.base import SafetyPolicy
from packages.safety_policies.config import PolicyConfig
from packages.schemas.canonical import (
    Alert,
    AlertLevel,
    AlertStatus,
    RiskEvent,
    RiskType,
    VehicleState,
)


# Risk types that represent imminent collision scenarios and are eligible
# to pre-empt lower-priority alerts.
_COLLISION_RISK_TYPES: set[RiskType] = {
    RiskType.COLLISION,
    RiskType.WRONG_WAY,
    RiskType.EMERGENCY_BRAKING,
    RiskType.INTERSECTION_CONFLICT,
}

# Vulnerability multipliers for actor types in severity scoring.
_VULNERABILITY_WEIGHTS: dict[str, float] = {
    "PEDESTRIAN": 1.5,
    "CYCLIST": 1.4,
    "BIKE": 1.3,
    "AUTO": 1.1,
    "CAR": 1.0,
    "BUS": 0.9,
    "TRUCK": 0.9,
    "AMBULANCE": 0.8,
    "FIRE_TRUCK": 0.8,
    "POLICE": 0.8,
    "OTHER": 1.0,
}

# Human-readable titles per risk type.  Wording deliberately avoids
# certainty claims (e.g. "Possible" / "Potential" prefixes).
_ALERT_TITLES: dict[RiskType, str] = {
    RiskType.COLLISION: "Possible collision ahead",
    RiskType.INTERSECTION_CONFLICT: "Potential intersection conflict",
    RiskType.BLIND_CURVE: "Limited visibility on curve ahead",
    RiskType.BLIND_INTERSECTION: "Reduced visibility at intersection",
    RiskType.WRONG_WAY: "Possible wrong-way vehicle detected",
    RiskType.EMERGENCY_BRAKING: "Vehicle braking hard ahead",
    RiskType.STALLED_VEHICLE: "Possible stalled vehicle ahead",
    RiskType.ANIMAL_CROSSING: "Possible animal near roadway",
    RiskType.PEDESTRIAN_CONFLICT: "Pedestrian activity detected nearby",
    RiskType.ROAD_HAZARD: "Road hazard reported ahead",
    RiskType.EMERGENCY_VEHICLE: "Emergency vehicle approaching",
    RiskType.ROAD_NARROWING: "Road narrows ahead",
}

# Concise driver-facing descriptions per risk type.
_ALERT_DESCRIPTIONS: dict[RiskType, str] = {
    RiskType.COLLISION: "Sensors indicate a potential collision risk. Proceed with caution.",
    RiskType.INTERSECTION_CONFLICT: "Conflicting traffic may be approaching this intersection.",
    RiskType.BLIND_CURVE: "Oncoming traffic may be hidden by the curve. Reduce speed.",
    RiskType.BLIND_INTERSECTION: "Cross-traffic visibility is limited. Approach slowly.",
    RiskType.WRONG_WAY: "A vehicle may be travelling against traffic on your route.",
    RiskType.EMERGENCY_BRAKING: "A vehicle ahead has braked suddenly. Be prepared to stop.",
    RiskType.STALLED_VEHICLE: "A stopped vehicle may be blocking the road ahead.",
    RiskType.ANIMAL_CROSSING: "An animal has been detected near the roadway. Slow down.",
    RiskType.PEDESTRIAN_CONFLICT: "A pedestrian has been detected near your path.",
    RiskType.ROAD_HAZARD: "A road hazard has been reported on your route.",
    RiskType.EMERGENCY_VEHICLE: "An emergency vehicle is nearby. Prepare to yield.",
    RiskType.ROAD_NARROWING: "The road narrows ahead. Adjust your position.",
}


class AlertPrioritizer(SafetyPolicy):
    """Converts RiskEvents into prioritized Alerts with lifecycle management.

    Priority scoring considers: time-to-conflict, severity estimate,
    confidence, braking feasibility, actor vulnerability, message age,
    and duplication/suppression state.

    This class is a SafetyPolicy subclass (not a SafetyDetector).
    """

    def __init__(self, config: PolicyConfig) -> None:
        self._config = config.alert_prioritization
        self._policy_version = config.version

        # Suppression tracking:
        #   suppression_key -> last_alert_ts
        self._suppression_log: dict[str, datetime] = {}

        # Active alert cache for hysteresis:
        #   risk_id -> (alert, last_risk_score)
        self._active_cache: dict[str, tuple[Alert, float]] = {}

        # Internal alert store for single-risk evaluate_risk lifecycle:
        #   alert_id -> Alert
        self._alert_store: dict[str, Alert] = {}
        # Per-actor active alert count (for max_concurrent enforcement)
        self._actor_alert_counts: dict[str, int] = {}
        # Per-suppression-key risk score cache (for hysteresis in evaluate_risk)
        self._risk_score_cache: dict[str, float] = {}

    # -- SafetyPolicy required properties ------------------------------------

    @property
    def name(self) -> str:
        return "alert_prioritizer"

    @property
    def version(self) -> str:
        return self._policy_version

    # -- Public API ----------------------------------------------------------

    def prioritize(
        self,
        risks: list[RiskEvent],
        active_alerts: list[Alert],
        actor_states: dict[str, Any],
    ) -> list[Alert]:
        """Main entry point: convert a batch of RiskEvents into a prioritized
        list of Alerts.

        Args:
            risks: Current-tick risk events from detectors.
            active_alerts: Previously issued alerts still considered active.
            actor_states: Mapping of actor_id to VehicleState dict or object,
                used for vulnerability and braking-feasibility scoring.

        Returns:
            Prioritized list of Alert objects (highest priority first),
            including updated/resolved alerts and newly created ones.
        """
        now = datetime.now(timezone.utc)

        # Index active alerts by risk_id for fast lookup
        active_by_risk: dict[str, Alert] = {
            a.risk_id: a for a in active_alerts if a.status == AlertStatus.ACTIVE
        }

        # Track which risk_ids are still present this tick
        current_risk_ids: set[str] = {r.risk_id for r in risks}

        output_alerts: list[Alert] = []

        # -- Process each incoming risk --------------------------------------
        for risk in risks:
            suppression_key = self._make_suppression_key(risk)

            if self.should_suppress(risk, active_alerts):
                continue

            existing = active_by_risk.get(risk.risk_id)
            if existing is not None:
                # Hysteresis check: only update if score changed meaningfully
                cached = self._active_cache.get(risk.risk_id)
                if cached is not None:
                    _, prev_score = cached
                    if abs(risk.risk_score - prev_score) < self._config.hysteresis_threshold:
                        # Score has not changed enough -- keep existing alert
                        output_alerts.append(existing)
                        continue

                # Update existing alert
                updated = self._update_alert(existing, risk, actor_states, now)
                output_alerts.append(updated)
                self._active_cache[risk.risk_id] = (updated, risk.risk_score)
                self._suppression_log[suppression_key] = now
            else:
                # Create new alert
                alert = self._create_alert(risk, actor_states, now)
                if alert is not None:
                    output_alerts.append(alert)
                    self._active_cache[risk.risk_id] = (alert, risk.risk_score)
                    self._suppression_log[suppression_key] = now

        # -- Resolve alerts whose risks are no longer present ----------------
        for risk_id, alert in active_by_risk.items():
            if risk_id not in current_risk_ids:
                resolved = alert.model_copy(
                    update={
                        "status": AlertStatus.RESOLVED,
                        "ts": now,
                    }
                )
                output_alerts.append(resolved)
                self._active_cache.pop(risk_id, None)

        # -- Expire old suppression entries ----------------------------------
        self._prune_suppression_log(now)

        # -- Sort by priority score (highest first) --------------------------
        output_alerts.sort(key=lambda a: self._alert_sort_key(a), reverse=True)

        # -- Apply pre-emption: critical collision alerts suppress lower ------
        output_alerts = self._apply_preemption(output_alerts)

        # -- Enforce max concurrent alerts per actor -------------------------
        output_alerts = self._enforce_max_concurrent(output_alerts)

        return output_alerts

    # -- SafetyPolicy abstract method implementations ------------------------

    def evaluate_risk(
        self, risk: RiskEvent, context: dict[str, Any]
    ) -> Alert | None:
        """Convert a single RiskEvent into an Alert.

        This satisfies the SafetyPolicy ABC. For batch processing prefer
        the ``prioritize`` method. Handles suppression, hysteresis,
        max-concurrent enforcement, and critical pre-emption internally.
        """
        actor_states = context.get("actor_states", {})
        active_alerts = context.get("active_alerts", [])
        now = datetime.now(timezone.utc)

        if self.should_suppress(risk, active_alerts):
            return None

        suppression_key = self._make_suppression_key(risk)

        # Hysteresis: small risk score changes do not create new alerts
        cached_score = self._risk_score_cache.get(suppression_key)
        if cached_score is not None:
            if abs(risk.risk_score - cached_score) < self._config.hysteresis_threshold:
                return None
        self._risk_score_cache[suppression_key] = risk.risk_score

        # Max concurrent alert enforcement
        for actor_id in risk.affected_actor_ids:
            count = self._actor_alert_counts.get(actor_id, 0)
            if count >= self._config.max_concurrent_alerts:
                lowest = self._find_lowest_alert_for_actor(actor_id, active_alerts)
                if lowest and self._level_rank(self._compute_level(risk, actor_states)) > self._level_rank(lowest.level):
                    self._resolve_stored_alert(lowest.alert_id)
                else:
                    return None

        alert = self._create_alert(risk, actor_states, now)
        if alert is None:
            return None

        # Critical collision pre-emption
        level = alert.level
        if self._config.critical_preempts_advisory and level == AlertLevel.CRITICAL:
            self._suppress_lower_priority(risk.affected_actor_ids, active_alerts)

        # Track in internal store
        self._alert_store[alert.alert_id] = alert
        self._suppression_log[suppression_key] = now
        for actor_id in risk.affected_actor_ids:
            self._actor_alert_counts[actor_id] = (
                self._actor_alert_counts.get(actor_id, 0) + 1
            )

        return alert

    def get_active_alerts(self) -> list[Alert]:
        """Return all currently active alerts from the internal store."""
        return [
            a for a in self._alert_store.values()
            if a.status == AlertStatus.ACTIVE
        ]

    def resolve_risk(self, risk_id: str) -> Alert | None:
        """Resolve alerts associated with a risk that is no longer active."""
        for alert_id, alert in list(self._alert_store.items()):
            if alert.risk_id == risk_id and alert.status == AlertStatus.ACTIVE:
                resolved = alert.model_copy(
                    update={"status": AlertStatus.RESOLVED}
                )
                self._alert_store[alert_id] = resolved
                for actor_id in alert.affected_actor_ids:
                    self._actor_alert_counts[actor_id] = max(
                        0, self._actor_alert_counts.get(actor_id, 1) - 1
                    )
                return resolved
        return None

    def should_suppress(
        self, risk: RiskEvent, active_alerts: list[Alert]
    ) -> bool:
        """Check whether this risk should be suppressed to prevent
        duplicate/redundant alerting.
        """
        suppression_key = self._make_suppression_key(risk)
        last_sent = self._suppression_log.get(suppression_key)
        if last_sent is not None:
            elapsed = (datetime.now(timezone.utc) - last_sent).total_seconds()
            if elapsed < self._config.suppression_window_s:
                return True
        return False

    # -- Internal helpers ----------------------------------------------------

    def _resolve_stored_alert(self, alert_id: str) -> None:
        """Resolve an alert in the internal store."""
        alert = self._alert_store.get(alert_id)
        if alert and alert.status == AlertStatus.ACTIVE:
            self._alert_store[alert_id] = alert.model_copy(
                update={"status": AlertStatus.RESOLVED}
            )
            for actor_id in alert.affected_actor_ids:
                self._actor_alert_counts[actor_id] = max(
                    0, self._actor_alert_counts.get(actor_id, 1) - 1
                )

    def _suppress_lower_priority(
        self, actor_ids: list[str], active_alerts: list[Alert]
    ) -> None:
        """Suppress lower-priority active alerts for the given actors."""
        for alert in active_alerts:
            if alert.status != AlertStatus.ACTIVE:
                continue
            if alert.level in (AlertLevel.ADVISORY, AlertLevel.INFORMATIONAL):
                if any(aid in alert.affected_actor_ids for aid in actor_ids):
                    # Resolve in internal store if present
                    self._resolve_stored_alert(alert.alert_id)

    @staticmethod
    def _find_lowest_alert_for_actor(
        actor_id: str, active_alerts: list[Alert]
    ) -> Alert | None:
        """Find the lowest-priority active alert for an actor."""
        priority_order = [
            AlertLevel.INFORMATIONAL,
            AlertLevel.ADVISORY,
            AlertLevel.WARNING,
            AlertLevel.CRITICAL,
        ]
        for level in priority_order:
            for alert in active_alerts:
                if (
                    alert.status == AlertStatus.ACTIVE
                    and alert.level == level
                    and actor_id in alert.affected_actor_ids
                ):
                    return alert
        return None

    @staticmethod
    def _level_rank(level: AlertLevel) -> int:
        """Return numeric rank for an alert level."""
        return {
            AlertLevel.INFORMATIONAL: 0,
            AlertLevel.ADVISORY: 1,
            AlertLevel.WARNING: 2,
            AlertLevel.CRITICAL: 3,
        }.get(level, 0)

    def _create_alert(
        self,
        risk: RiskEvent,
        actor_states: dict[str, Any],
        now: datetime,
    ) -> Alert | None:
        """Build a new Alert from a RiskEvent."""
        level = self._compute_level(risk, actor_states)
        title = _ALERT_TITLES.get(risk.type, "Safety alert")
        description = _ALERT_DESCRIPTIONS.get(
            risk.type, "A safety condition has been detected on your route."
        )

        # Machine reasoning (stored in evidence, not shown to driver)
        machine_evidence = {
            "type": "prioritization_reasoning",
            "risk_score": risk.risk_score,
            "severity": risk.severity,
            "confidence": risk.confidence,
            "time_to_conflict_s": risk.time_to_conflict_s,
            "vulnerability_factor": self._max_vulnerability(
                risk.affected_actor_ids, actor_states
            ),
            "braking_feasibility": self._braking_feasibility(
                risk, actor_states
            ),
            "level_assigned": level.value,
            "policy_version": self._policy_version,
        }

        combined_evidence = list(risk.evidence) + [machine_evidence]

        return Alert(
            risk_id=risk.risk_id,
            level=level,
            status=AlertStatus.ACTIVE,
            title=title,
            description=description,
            ts=now,
            affected_actor_ids=risk.affected_actor_ids,
            confidence=risk.confidence,
            evidence=combined_evidence,
            time_to_conflict_s=risk.time_to_conflict_s,
            expires_at=risk.expires_at,
            target_audience=risk.affected_actor_ids,
            policy_version=self._policy_version,
            suppression_key=self._make_suppression_key(risk),
        )

    def _update_alert(
        self,
        existing: Alert,
        risk: RiskEvent,
        actor_states: dict[str, Any],
        now: datetime,
    ) -> Alert:
        """Update an existing alert with new risk data."""
        new_level = self._compute_level(risk, actor_states)

        machine_evidence = {
            "type": "prioritization_reasoning",
            "risk_score": risk.risk_score,
            "severity": risk.severity,
            "confidence": risk.confidence,
            "time_to_conflict_s": risk.time_to_conflict_s,
            "update_reason": "risk_score_changed",
            "previous_level": existing.level.value,
            "new_level": new_level.value,
            "policy_version": self._policy_version,
        }

        combined_evidence = list(risk.evidence) + [machine_evidence]

        return existing.model_copy(
            update={
                "level": new_level,
                "ts": now,
                "confidence": risk.confidence,
                "evidence": combined_evidence,
                "time_to_conflict_s": risk.time_to_conflict_s,
                "expires_at": risk.expires_at,
                "affected_actor_ids": risk.affected_actor_ids,
                "target_audience": risk.affected_actor_ids,
            }
        )

    def _compute_level(
        self,
        risk: RiskEvent,
        actor_states: dict[str, Any],
    ) -> AlertLevel:
        """Compute alert level from risk properties, vulnerability, and
        braking feasibility.

        The composite priority score blends:
        - risk_score (severity * confidence)
        - time-to-conflict urgency
        - actor vulnerability
        - braking feasibility
        """
        priority = self._compute_priority_score(risk, actor_states)

        if priority >= 0.8:
            return AlertLevel.CRITICAL
        elif priority >= 0.5:
            return AlertLevel.WARNING
        elif priority >= 0.2:
            return AlertLevel.ADVISORY
        return AlertLevel.INFORMATIONAL

    def _compute_priority_score(
        self,
        risk: RiskEvent,
        actor_states: dict[str, Any],
    ) -> float:
        """Composite priority score in [0, 1]."""
        # Base: risk_score already combines severity and confidence
        base = risk.risk_score

        # Time-to-conflict urgency: shorter time = higher urgency
        ttc_factor = 0.0
        if risk.time_to_conflict_s is not None and risk.time_to_conflict_s > 0:
            # Urgency peaks below 3 seconds, diminishes beyond 10
            ttc_factor = max(0.0, min(1.0, 1.0 - (risk.time_to_conflict_s / 10.0)))

        # Vulnerability of affected actors
        vuln = self._max_vulnerability(risk.affected_actor_ids, actor_states)

        # Braking feasibility: lower feasibility = more urgent
        braking = self._braking_feasibility(risk, actor_states)
        braking_urgency = 1.0 - braking  # invert: hard to brake = urgent

        # Weighted combination
        score = (
            0.35 * base
            + 0.25 * ttc_factor
            + 0.20 * (vuln / 1.5)  # normalize vulnerability weight
            + 0.20 * braking_urgency
        )
        return min(1.0, max(0.0, score))

    @staticmethod
    def _max_vulnerability(
        actor_ids: list[str],
        actor_states: dict[str, Any],
    ) -> float:
        """Return the maximum vulnerability weight among affected actors."""
        max_v = 1.0
        for aid in actor_ids:
            state = actor_states.get(aid)
            if state is None:
                continue
            actor_type = None
            if isinstance(state, dict):
                actor_type = state.get("actor_type")
            elif hasattr(state, "actor_type"):
                actor_type = state.actor_type
            if actor_type is not None:
                type_str = actor_type if isinstance(actor_type, str) else actor_type.value
                weight = _VULNERABILITY_WEIGHTS.get(type_str, 1.0)
                max_v = max(max_v, weight)
        return max_v

    @staticmethod
    def _braking_feasibility(
        risk: RiskEvent,
        actor_states: dict[str, Any],
    ) -> float:
        """Estimate braking feasibility [0, 1] for the affected actors.

        1.0 = easy to stop in time, 0.0 = impossible.
        Uses time-to-conflict and current speed.
        """
        if risk.time_to_conflict_s is None:
            return 0.5  # unknown -- neutral

        # Gather speeds of affected actors
        speeds: list[float] = []
        for aid in risk.affected_actor_ids:
            state = actor_states.get(aid)
            if state is None:
                continue
            speed = None
            if isinstance(state, dict):
                speed = state.get("speed_mps")
            elif hasattr(state, "speed_mps"):
                speed = state.speed_mps
            if speed is not None:
                speeds.append(speed)

        if not speeds:
            return 0.5

        max_speed = max(speeds)
        if max_speed <= 0:
            return 1.0

        # Comfortable deceleration ~ 3.5 m/s^2
        comfortable_decel = 3.5
        stopping_distance = (max_speed ** 2) / (2 * comfortable_decel)
        available_distance = max_speed * risk.time_to_conflict_s

        if available_distance <= 0:
            return 0.0

        ratio = available_distance / stopping_distance
        return min(1.0, max(0.0, ratio - 0.5))

    @staticmethod
    def _make_suppression_key(risk: RiskEvent) -> str:
        """Create a suppression key for deduplication.

        Key combines risk type + sorted affected actor IDs + segment.
        """
        actor_part = ",".join(sorted(risk.affected_actor_ids))
        segment_part = risk.road_segment_id or "none"
        return f"{risk.type.value}:{actor_part}:{segment_part}"

    def _prune_suppression_log(self, now: datetime) -> None:
        """Remove suppression entries older than 2x the suppression window."""
        cutoff = now - timedelta(seconds=self._config.suppression_window_s * 2)
        stale = [k for k, ts in self._suppression_log.items() if ts < cutoff]
        for k in stale:
            del self._suppression_log[k]

    def _apply_preemption(self, alerts: list[Alert]) -> list[Alert]:
        """If critical collision alerts exist, suppress lower-priority
        non-collision alerts for the same actors.

        Per Section 30: critical collision alerts pre-empt informational
        road hazards.
        """
        if not self._config.critical_preempts_advisory:
            return alerts

        # Collect actor IDs covered by critical collision alerts
        critical_actor_ids: set[str] = set()
        for alert in alerts:
            if alert.level == AlertLevel.CRITICAL and alert.status == AlertStatus.ACTIVE:
                # Check if the underlying risk type is collision-class via
                # suppression key prefix
                is_collision_class = False
                if alert.suppression_key:
                    for rt in _COLLISION_RISK_TYPES:
                        if alert.suppression_key.startswith(rt.value):
                            is_collision_class = True
                            break
                # Also check evidence for known collision-class detector types
                if not is_collision_class:
                    for ev in alert.evidence:
                        if isinstance(ev, dict) and ev.get("type") in (
                            "wrong_way_detection",
                            "emergency_braking",
                            "collision_detection",
                            "intersection_conflict",
                        ):
                            is_collision_class = True
                            break

                if is_collision_class:
                    critical_actor_ids.update(alert.affected_actor_ids)

        if not critical_actor_ids:
            return alerts

        result: list[Alert] = []
        for alert in alerts:
            if alert.level == AlertLevel.CRITICAL:
                result.append(alert)
                continue
            # Suppress lower-priority alerts for actors already covered
            overlap = set(alert.affected_actor_ids) & critical_actor_ids
            if overlap and alert.level in (
                AlertLevel.ADVISORY,
                AlertLevel.INFORMATIONAL,
            ):
                suppressed = alert.model_copy(
                    update={"status": AlertStatus.SUPPRESSED}
                )
                result.append(suppressed)
            else:
                result.append(alert)

        return result

    def _enforce_max_concurrent(self, alerts: list[Alert]) -> list[Alert]:
        """Enforce the maximum number of concurrent active alerts per actor.

        Lower-priority alerts beyond the limit are suppressed.
        """
        max_active = self._config.max_concurrent_alerts
        # Count active alerts per actor
        actor_counts: dict[str, int] = {}
        result: list[Alert] = []

        for alert in alerts:
            if alert.status != AlertStatus.ACTIVE:
                result.append(alert)
                continue

            # Check if any affected actor has hit the limit
            over_limit = False
            for aid in alert.affected_actor_ids:
                if actor_counts.get(aid, 0) >= max_active:
                    over_limit = True
                    break

            if over_limit:
                suppressed = alert.model_copy(
                    update={"status": AlertStatus.SUPPRESSED}
                )
                result.append(suppressed)
            else:
                result.append(alert)
                for aid in alert.affected_actor_ids:
                    actor_counts[aid] = actor_counts.get(aid, 0) + 1

        return result

    @staticmethod
    def _alert_sort_key(alert: Alert) -> tuple[int, float, float]:
        """Sort key: (level_rank, confidence, inverse_age).

        Higher values = higher priority.
        """
        level_rank = {
            AlertLevel.CRITICAL: 4,
            AlertLevel.WARNING: 3,
            AlertLevel.ADVISORY: 2,
            AlertLevel.INFORMATIONAL: 1,
        }
        # Active alerts sort above resolved/suppressed
        status_rank = 1 if alert.status == AlertStatus.ACTIVE else 0

        return (
            level_rank.get(alert.level, 0) + status_rank * 10,
            alert.confidence,
            alert.ts.timestamp() if alert.ts else 0.0,
        )
