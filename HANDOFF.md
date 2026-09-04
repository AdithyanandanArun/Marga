# Handoff: first safety slice

## Current state

Branch `integration/first-slice-safety` contains the first executable vertical slice:

`SUMO-shaped adapter event → canonical VehicleState → gateway `/safety` mount → Hrishi detector → evidence-bearing RiskEvent → prioritized Alert`

The production import path is `services.safety_detectors`; it wraps the historical `services/safety-detectors` directory and exposes all 10 components. `services/integration/canonical_bridge.py` is the adapter boundary—extend it for new adapter payloads instead of adding parallel schemas.

## Verification

`458 passed` with `pytest tests/`; focused first-slice and safety suites pass; Ruff passes for changed files. The first-slice test is `tests/integration/test_first_safety_slice.py`.

## Continue here

1. Add world-state persistence/streaming to the gateway path.
2. Replace the emergency-braking-only assertion with a multi-actor risk scenario and live alert WebSocket assertion.
3. Migrate the legacy safety service from FastAPI `on_event` startup to a lifespan handler.
4. Run `make typecheck`; the existing baseline still reports unrelated strict-MyPy errors in Agent 2 services.

No credentials or co-author trailers were added.
