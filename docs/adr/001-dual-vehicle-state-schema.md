# ADR 001 — Two VehicleState schemas

**Status:** Accepted

## Context

The simulation adapter produces `VehicleState` (in `services/simulation_adapter/schemas.py`) and the safety detectors consume `VehicleState` (in `packages/schemas/canonical.py`). These are separate Pydantic models with overlapping but not identical fields.

## Decision

Keep both models. The adapter's schema is SUMO-native (uses `PositionEstimate`, `scenario_run_id`, `trace_id`). The canonical schema is safety-detector-native (`actor_id`, `actor_type`, `capabilities`). The `canonical_bridge.py` converts between them at the gateway boundary.

## Consequences

- Adding a field to the adapter schema requires a matching change in `canonical_bridge.py` to propagate it.
- The bridge is the single point of cross-cutting concern and should be kept thin.
- Any team member changing either schema must check and update the bridge.
