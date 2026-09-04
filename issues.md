# Marga — Consolidated Issues

This is the execution list derived from `README.md`, `AGENTS.md`, `round_2.md`,
and the current demo audit. Priority reflects what can make the hackathon demo
credible first, followed by platform completeness.

## P0 — Demo credibility blockers

### P0.1 Dashboard is not connected to the real world stream

`Dashboard.tsx` never creates or starts `WorldStream`. The map therefore falls
back to the browser fixture instead of showing the gateway's canonical state.

**Fix:** start `WorldStream` from the dashboard lifecycle, expose connection
state, and stop the fallback when the gateway WebSocket connects.

### P0.2 Browser fixture invents safety outcomes

`apps/web-dashboard/src/net/fixtures.ts` randomly generates vehicle positions,
risks, alerts, and metrics. This violates the manual's source-of-truth rule and
makes the demo look like unrelated moving dots.

**Fix:** use fixtures only for explicit offline mode. Remove random risk/alert
generation from the normal path; alerts must arrive from backend detectors.

### P0.3 No coherent, road-constrained demo scenario

Vehicles are scattered across the map and change heading randomly. There is no
clear beginning, conflict, warning, and resolution that a judge can follow.

**Fix:** add a deterministic Bangalore junction scenario with route waypoints,
lane-following movement, a braking/pedestrian conflict, TTC countdown, and a
resolved outcome. Keep it as valid scenario input through the real pipeline, not
an `if demo_case` branch.

### P0.4 Alert explanation is not visible during the demo

The UI does not clearly show the risk evidence, TTC, uncertainty, trace ID, or
why an alert was issued.

**Fix:** make the alert panel show “why,” TTC, confidence, contributing actors,
position uncertainty, policy version, and a link to the incident trace.

### P0.5 Reroute action is not connected to the alert panel

The backend reroute endpoint exists, but the frontend has no Accept Reroute action,
route overlay, or alert resolution flow.

**Fix:** call `/v1/world-state/reroute`, render the returned route geometry, and
resolve affected alerts after acceptance.

## P1 — Contract and integration issues

### P1.1 README and backend endpoint names disagree

The README describes `/v1/world/snapshot` and `/v1/stream/world`, while the
backend serves `/v1/world-state/snapshot` and `/v1/world-state/stream`.

**Fix:** choose one canonical API, update the README and frontend together, and
retain compatibility aliases only with a migration note.

### P1.2 Alert delivery is not end-to-end verified

There is no `tests/e2e/` suite proving actor input → risk detection → alert →
WebSocket delivery without the fixture player.

**Fix:** add an in-process HTTP/WebSocket E2E test using arbitrary canonical
vehicle states and assert the complete event chain.

### P1.3 Incident traces are not durable

`GET /v1/incidents/{id}/trace` currently reads a bounded in-memory registry.
Restarting the gateway loses the evidence required by the manual's replayability
and explainability requirements.

**Fix:** persist risk evidence and decision traces through the persistence layer
and add a replay/trace integration test.

### P1.4 Scenario Studio is not the recommended demo entry point

The Run Scenario path and the live dashboard are not presented as one connected
flow to the user.

**Fix:** make Run create/start a scenario, show run state, and stream its events
into the same dashboard world store.

### P1.5 Fixture metrics are fabricated

The fallback produces random update rates, lag, risk rates, and alert counts.

**Fix:** source metrics from gateway/detector instrumentation; display “offline”
or “unavailable” when metrics are not connected.

## P1 — Safety and map presentation

### P1.6 Vehicle motion is visually unconvincing

Vehicles are rendered as generic dots without route context, lane position, or
motion history.

**Fix:** add oriented vehicle glyphs, short trails, route/lane geometry, and a
camera follow mode for the affected actor.

### P1.7 Map clutter and zoom-level behavior need tuning

Pedestrians, animals, hazards, and infrastructure compete for attention at the
same scale.

**Fix:** add zoom-level LOD, show only safety-relevant entities near the active
incident, and use a focused-junction initial viewport.

### P1.8 Traffic signal and infrastructure state needs backend parity

Signal positions are now stable in the frontend, but live infrastructure state
must use the same canonical position contract as fixture state.

**Fix:** add a contract test from backend `InfrastructureState` to the dashboard
signal layer.

## P2 — Platform reliability and operations

### P2.1 NATS JetStream is provisioned but unused

Canonical actor, risk, and alert events are still direct HTTP/WebSocket only.

**Fix:** publish/subscribe event envelopes on configured NATS subjects with a
graceful offline fallback.

### P2.2 Redis is provisioned but unused

Actor TTL expiry, alert deduplication, and rate limiting are not implemented.

**Fix:** add actor TTL expiry to live world state and tests for stale actor removal.

### P2.3 Database migrations are not smoke-tested in CI

Alembic/PostGIS exists but migration execution is not a required CI check.

**Fix:** run migrations against the PostGIS service container in CI.

### P2.4 OpenTelemetry is not configured end-to-end

The gateway has guarded instrumentation, but exporter configuration and context
propagation across service boundaries are incomplete.

**Fix:** configure `OTEL_EXPORTER_OTLP_ENDPOINT`, spans, and propagation tests.

### P2.5 Service metrics are incomplete

Safety, scenario, alert, hazard, and trust services do not expose the metrics
needed by `SystemMetrics`.

**Fix:** instrument detector latency, alert rate, hazard count, trust rejections,
and dropped messages.

### P2.6 Compose does not start application services

`infra/docker-compose.yml` starts Postgres, Redis, and NATS only. The Makefile
also references a nonexistent `infra/compose/docker-compose.yml` path.

**Fix:** add gateway/scenario services and correct the Makefile compose path.

### P2.7 Strict typecheck is not green

`make typecheck` still reports baseline errors in service packages.

**Fix:** resolve the errors and make the CI typecheck job required.

## P2 — Scenario and replay completeness

### P2.8 Replay UI is not connected to recorded events

The replay view has a scrubber but no complete backend event playback contract.

**Fix:** persist canonical run events and connect the scrubber to timestamp-windowed
`GET /v1/scenarios/{scenario_id}/runs/{run_id}/events`.

### P2.9 Real-adapter parity needs stronger acceptance tests

GNSS, OBU, RSU, and phone-GPS stubs exist, but adapter replacement should be
validated against the same canonical event and safety tests.

**Fix:** add parity tests that run each adapter through the same risk pipeline.

### P2.10 Scale and load results are not documented

Load generation and libsumo tests exist, but there is no recorded throughput,
latency, or detector behavior report for the Bangalore scenario.

**Fix:** run the benchmark, save reproducible results, and show them in the
technical handoff rather than presenting random dashboard metrics.

## P3 — Documentation and operator UX

### P3.1 Missing API/runbook/ADR documentation

The repository lacks the planned `docs/api.md`, `docs/runbook.md`, and ADRs.

**Fix:** document canonical contracts, local startup, production startup, and
decisions around schemas, spatial range, and event transport.

### P3.2 Driver commands are not wired

Driver Console controls do not call vehicle or signal command endpoints.

**Fix:** expose validated command endpoints and connect the console actions.

### P3.3 Scenario map placement is hardcoded

Scenario Studio adds actors at a fixed location instead of using a map click.

**Fix:** add map-click placement mode and persist the selected coordinates.

## Demo acceptance checklist

- [ ] A judge can open one URL and see a focused, road-constrained scenario.
- [ ] The browser is consuming backend WebSocket state, not generating safety outcomes.
- [ ] One visible conflict progresses from normal traffic to TTC warning to resolution.
- [ ] The alert explains evidence, uncertainty, confidence, and policy version.
- [ ] Accept Reroute draws a route and clears the related alert.
- [ ] The presenter can show the incident trace and test result in under one minute.
- [ ] No random metrics, random alerts, or teleporting actors are visible.
