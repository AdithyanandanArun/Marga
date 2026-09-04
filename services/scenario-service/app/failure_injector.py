"""
Failure injection engine for the Marga scenario service.

The FailureInjector is deliberately stateless with respect to the simulation:
it takes the current simulation time and the scenario's failure schedule and
returns the effects that should be applied at that instant.  This design means
that all callers — the real core, the test harness, and replay — go through
exactly the same code path.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from .schemas import FailureScheduleEntry, FailureType, PositionEstimate


class FailureEffect(BaseModel):
    """The runtime effect of a single active failure entry."""

    entry_id: str
    failure_type: FailureType
    parameters: dict[str, Any]
    started_at_sim_time_s: float


class ActiveFailureState(BaseModel):
    """Snapshot of all currently active failure effects."""

    effects: list[FailureEffect] = []


class FailureInjector:
    """
    Computes and applies the effects of scheduled failures at a given sim time.

    All methods are pure functions (no side-effects, no shared mutable state).
    """

    # ------------------------------------------------------------------
    # Core: compute active failures
    # ------------------------------------------------------------------

    def get_active_failures(
        self,
        schedule: list[FailureScheduleEntry],
        sim_time_s: float,
    ) -> list[FailureEffect]:
        """
        Return the subset of the failure schedule that is active at *sim_time_s*.

        A failure is active when:
            start_sim_time_s <= sim_time_s < start_sim_time_s + duration_s
        If duration_s is None the failure is active until the scenario ends.
        """
        active: list[FailureEffect] = []
        for entry in schedule:
            if entry.start_sim_time_s > sim_time_s:
                continue  # not started yet
            if entry.duration_s is not None:
                end = entry.start_sim_time_s + entry.duration_s
                if sim_time_s >= end:
                    continue  # already expired
            active.append(
                FailureEffect(
                    entry_id=entry.entry_id,
                    failure_type=entry.failure_type,
                    parameters=entry.parameters,
                    started_at_sim_time_s=entry.start_sim_time_s,
                )
            )
        return active

    # ------------------------------------------------------------------
    # GPS / Position
    # ------------------------------------------------------------------

    def apply_gps_degradation(
        self,
        position: PositionEstimate,
        effects: list[FailureEffect],
        actor_id: str | None = None,
    ) -> PositionEstimate:
        """
        Apply any active GPS-degradation effects to *position*.

        The worst (largest) uncertainty among all matching effects wins.
        Confidence is reduced proportionally to the uncertainty increase.

        actor_id is used to check whether this actor is in the affected set;
        if None the actor_id from *position* is used.
        """
        resolved_actor_id = actor_id or position.actor_id
        worst_uncertainty = position.uncertainty_m
        worst_confidence = position.confidence

        for effect in effects:
            if effect.failure_type != FailureType.gps_degradation:
                continue
            actor_filter: list[str] = effect.parameters.get("affected_actors", ["all"])
            if "all" not in actor_filter and resolved_actor_id not in actor_filter:
                continue

            uncertainty = float(effect.parameters.get("uncertainty_m", 50.0))
            # Confidence penalty scales linearly with extra uncertainty (capped at 1.0).
            confidence_penalty = min(1.0, uncertainty / 100.0)
            new_confidence = max(0.0, position.confidence - confidence_penalty)

            if uncertainty > worst_uncertainty:
                worst_uncertainty = uncertainty
                worst_confidence = min(worst_confidence, new_confidence)

        if worst_uncertainty == position.uncertainty_m and worst_confidence == position.confidence:
            return position  # nothing changed

        return position.model_copy(
            update={
                "uncertainty_m": worst_uncertainty,
                "confidence": worst_confidence,
            }
        )

    # ------------------------------------------------------------------
    # Connectivity
    # ------------------------------------------------------------------

    def get_offline_services(self, effects: list[FailureEffect]) -> set[str]:
        """
        Return the set of service names that are offline due to connectivity
        failures active in *effects*.
        """
        offline: set[str] = set()
        for effect in effects:
            if effect.failure_type == FailureType.connectivity_loss:
                offline.update(effect.parameters.get("affected_services", []))
        return offline

    # ------------------------------------------------------------------
    # RSU
    # ------------------------------------------------------------------

    def is_rsu_operational(self, rsu_id: str, effects: list[FailureEffect]) -> bool:
        """Return False if *rsu_id* is currently failed."""
        for effect in effects:
            if effect.failure_type == FailureType.rsu_failure:
                if effect.parameters.get("rsu_id") == rsu_id:
                    return False
        return True

    # ------------------------------------------------------------------
    # Road conditions
    # ------------------------------------------------------------------

    def get_road_events_from_failures(
        self,
        effects: list[FailureEffect],
        sim_time_s: float,  # noqa: ARG002  (available for future time-aware events)
    ) -> list[dict]:
        """
        Return road-event commands to apply to the simulation adapter.

        Each dict is an opaque command understood by the adapter:
          {"edge_id": ..., "event_type": "close"|"narrow", "value": ...}
        """
        events: list[dict] = []
        for effect in effects:
            if effect.failure_type == FailureType.road_closure:
                events.append(
                    {
                        "edge_id": effect.parameters.get("edge_id"),
                        "event_type": "close",
                        "value": None,
                    }
                )
            elif effect.failure_type == FailureType.road_narrowing:
                events.append(
                    {
                        "edge_id": effect.parameters.get("edge_id"),
                        "event_type": "narrow",
                        "value": effect.parameters.get("lanes_remaining", 1),
                    }
                )
        return events

    # ------------------------------------------------------------------
    # Injected actors
    # ------------------------------------------------------------------

    def get_injected_actors(self, effects: list[FailureEffect]) -> list[dict]:
        """
        Return actor-injection commands for the simulation adapter.

        Each dict describes an actor that should be inserted into the simulation.
        """
        actors: list[dict] = []
        for effect in effects:
            if effect.failure_type == FailureType.malicious_input:
                actors.append(
                    {
                        "actor_id": effect.parameters.get("actor_id"),
                        "payload_type": effect.parameters.get(
                            "payload_type", "spoofed_position"
                        ),
                        "parameters": effect.parameters,
                    }
                )
            elif effect.failure_type == FailureType.animal_crossing:
                actors.append(
                    {
                        "actor_id": f"animal_{effect.entry_id}",
                        "actor_type": effect.parameters.get("actor_type", "cow"),
                        "edge_id": effect.parameters.get("edge_id"),
                        "count": effect.parameters.get("count", 1),
                    }
                )
            elif effect.failure_type == FailureType.wrong_way_vehicle:
                actors.append(
                    {
                        "actor_id": f"wrongway_{effect.entry_id}",
                        "edge_id": effect.parameters.get("edge_id"),
                        "vehicle_type": effect.parameters.get("vehicle_type", "car"),
                    }
                )
            elif effect.failure_type == FailureType.stalled_vehicle:
                actors.append(
                    {
                        "actor_id": f"stalled_{effect.entry_id}",
                        "edge_id": effect.parameters.get("edge_id"),
                        "vehicle_type": effect.parameters.get("vehicle_type", "car"),
                        "lane": effect.parameters.get("lane", 0),
                    }
                )
            elif effect.failure_type == FailureType.emergency_vehicle:
                actors.append(
                    {
                        "actor_id": f"emergency_{effect.entry_id}",
                        "edge_id": effect.parameters.get("edge_id"),
                        "vehicle_type": effect.parameters.get(
                            "vehicle_type", "ambulance"
                        ),
                        "priority": True,
                    }
                )
        return actors

    # ------------------------------------------------------------------
    # Environment overrides
    # ------------------------------------------------------------------

    def get_environment_override(self, effects: list[FailureEffect]) -> dict:
        """
        Return environment condition overrides derived from active failures.

        The worst (lowest) visibility wins when multiple weather effects are active.
        """
        overrides: dict = {}
        worst_visibility: float | None = None
        worst_condition: str | None = None

        for effect in effects:
            if effect.failure_type == FailureType.weather_visibility:
                vis = float(effect.parameters.get("visibility_m", 100.0))
                if worst_visibility is None or vis < worst_visibility:
                    worst_visibility = vis
                    worst_condition = effect.parameters.get("condition", "fog")
            elif effect.failure_type == FailureType.traffic_density_spike:
                overrides.setdefault("traffic_density_overrides", []).append(
                    {
                        "edge_ids": effect.parameters.get("edge_ids", []),
                        "density_factor": effect.parameters.get("density_factor", 2.0),
                    }
                )

        if worst_visibility is not None:
            overrides["visibility_m"] = worst_visibility
            overrides["precipitation"] = worst_condition

        return overrides
