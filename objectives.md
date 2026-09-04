# Marga — Remaining Objectives

Cross-referenced against the engineering build manual (README.md, AGENTS.md roadmap).
Items marked ✅ are done. Everything below is not yet implemented.

---

## Section 1 — Adithyan Agent 1: World State, Trajectories, Collision Risk

Owner: **Adithyan — Agent 1** (world state, geospatial/position core, trajectories, collision risk, evidence)

### 1.1 World-state WebSocket emits wrong format
**Status: broken.** The frontend (`worldStream.ts`) expects `WorldDelta` messages:
```json
{ "kind": "snapshot", "server_time": "...", "upserts": [...], "deletes": [] }
```
The backend (`/v1/world-state/stream`) sends `{"actors": [...]}`. The frontend store's
`applyDelta()` never fires with real data. Fix: rewrite `world_state.py` stream to emit
proper `WorldDelta`-shaped payloads keyed by `entity_type` and `entity_id`.

### 1.2 Canonical ingestion gateway endpoints missing
README promises:
```
POST /v1/ingest/vehicle-state
POST /v1/ingest/pedestrian-state
POST /v1/ingest/hazard-observation
```
None exist on the gateway. Currently the only ingest path is `POST /v1/world-state/ingest`
(our interim endpoint). Real adapters and the frontend need the canonical paths.

### 1.3 Trajectory prediction and TTC collision risk — not built
README roadmap item 4: *"Build generic trajectory and time-to-collision risk detection."*
- No trajectory extrapolation service exists.
- The frontend `showTrajectories` toggle has no data to render.
- The `REAR_END` / `INTERSECTION_CONFLICT` / `HEAD_ON` risk types shown in fixtures
  are fake — no real TTC computation exists.
- Need: `services/position/` trajectory engine; expose conflict events as `risk.detected`.

### 1.4 Incident trace endpoint missing
README API: `GET /v1/incidents/{id}/trace`
No endpoint exists. Alerts are currently in-memory only. When an alert fires, the
contributing `RiskEvent` evidence is not persisted with a trace ID. Needed for:
explainability, replay, and the build manual's "Explainable decisions" hard rule.

### 1.5 Position uncertainty fusion service missing
`services/position/` does not exist. Position uncertainty is passed through from SUMO
as a raw number but never fused from multiple sources (GNSS + RSU range + speed).
Offline-first requirement: when GPS degrades, uncertainty must propagate into every
downstream confidence score automatically.

---

## Section 2 — Adithyan Agent 2: Persistence, Transport, Observability, CI

Owner: **Adithyan — Agent 2** (hazard fusion, trust/security, offline transport, persistence, observability, CI/deploy)

### 2.1 NATS JetStream — in docker-compose, nothing uses it
`infra/docker-compose.yml` provisions NATS 2.10 with JetStream. No service publishes or
subscribes to any NATS subject. The canonical event families (`actor.state.updated`,
`risk.detected`, `alert.issued`, etc.) should be published to NATS so services can
subscribe independently. Currently all communication is direct HTTP/WebSocket only.

### 2.2 Redis — provisioned but unused
Redis 7 is in compose. No service reads or writes to it. Intended use: ephemeral world-state
(actor TTL expiry), rate limiting for trust service, alert deduplication window.
Implement at minimum: actor state TTL so stale actors expire from the live map.

### 2.3 Alembic migrations not applied/tested
`packages/persistence/alembic/` exists but migrations have never been run against a live
PostGIS instance in CI. `make bootstrap` skips on DB unavailability. The persistence
package has ORM models but no verified schema in the running database.
Needed: migration smoke test in CI using the PostGIS service container.

### 2.4 OpenTelemetry tracing not wired through services
`packages/observability/marga_observability/` exists. The gateway has a guarded
`FastAPIInstrumentor` import. No service actually configures an OTLP exporter or
propagates trace context between service calls. Every cross-service hop is currently
untraceable. Wire `OTEL_EXPORTER_OTLP_ENDPOINT` env → exporter → span propagation.

### 2.5 Prometheus metrics only on gateway, not on safety/scenario/alerts
`GET /metrics` exists on the gateway with request count and duration. The safety
detectors, scenario service, alerts service, and hazard fusion have no metrics exported.
Critical missing metrics: detector evaluation latency, alert issue rate, hazard count,
trust rejection rate — all referenced in `SystemMetrics` type in the frontend but
only populated by the fixture player with random numbers.

### 2.6 Docker image for gateway not wired into docker-compose
`Dockerfile` exists and builds the gateway image. `infra/docker-compose.yml` has only
infra services (postgres, redis, nats). The gateway, safety service, and scenario service
have no compose service definitions. Running the platform requires manual `uvicorn`
invocation. Add compose services for at minimum: gateway and scenario-service.

---

## Section 3 — Amritha: Simulation, Scenarios, Real-Adapter Stubs

Owner: **Amritha** (OSM/SUMO, simulation and world systems, deterministic scenarios, failure injection)

### 3.1 Scenario Studio "Run" does not wire to SUMO
The scenario-service has full CRUD (`POST /v1/scenarios`, `POST /v1/scenarios/{id}/runs`)
but `_start_run()` in `main.py` creates a `TimeController` only — it never instantiates
`SimulationRunner` or an adapter. Pressing Run in the Studio creates a scenario record
but no simulation starts.
Fix: when a run is created, spin up a `SimulationRunner` with a mock (or real SUMO)
adapter and push its events to the world-state store via `ingest_events()`.

### 3.2 Real-world adapter stubs missing
README: *"replacing a simulator feed with a real feed must require adapter/configuration
changes, not a rewrite of the core."* No real-world adapter stubs exist for:
- GNSS receiver (NMEA/u-blox over serial)
- OBU (on-board unit) via DSRC/C-V2X radio frame
- RSU (roadside unit) observation feed
- Phone GPS (iOS/Android background location)
These need to be thin Protocol-implementing stubs in `services/simulation-adapter/`
alongside the existing `sumo_traci.py` and `sumo_libsumo.py`.

### 3.3 libsumo scale path not validated
`sumo_libsumo.py` exists but has never been tested. For scale (>500 vehicles) the
SUMO documentation recommends libsumo over TraCI (no socket overhead). There is no
benchmark or CI job comparing TraCI vs libsumo throughput. Needed: a headless
integration test that runs the libsumo adapter for N ticks and asserts the event rate.

### 3.4 Load generation tooling missing
`tools/` has only `osm-import`. README roadmap item 7 mentions *"scale testing."*
No load generator exists to replay a recorded scenario at 10×, replay with many
simultaneous vehicles, or stress the safety detector pipeline.
Needed: `tools/load-gen/` — a script that replays a fixture file at configurable Hz
against the gateway's ingest endpoint.

### 3.5 Deterministic replay backend not wired
`services/scenario-service/app/time_control.py` has a `seek()` method for replay.
`services/trust/marga_trust/replay.py` has a `ReplayCache`. But `ReplayView.tsx`
has no backend to call. No endpoint exists to serve a recorded event sequence by
timestamp. The replay scrubber in the frontend is purely cosmetic.
Needed: persist canonical events to PostgreSQL during a run; expose
`GET /v1/scenarios/{id}/runs/{run_id}/events?from_s=&to_s=` for the replay view.

---

## Section 4 — Hrishi: Safety Evaluation, Frontend Safety Layers, E2E Tests

Owner: **Hrishi** (safety policies, acceptance evaluation, frontend safety features)

### 4.1 E2E test suite missing entirely
`tests/e2e/` does not exist. README and AGENTS.md both require E2E verification.
Needed: a Playwright (or httpx-based) suite that:
- starts the gateway in-process
- drives a scenario run end-to-end
- asserts that actor events → risk detection → alert → WebSocket delivery works
  without any fixture player involvement.

### 4.2 V2X links layer — toggle exists, layer does not
`showV2XLinks` toggle in `LayerControls.tsx` is wired to state but no layer function
reads it. No `createV2XLinksLayer()` exists. Without this, the "V2X Links" toggle
appears in the UI but does nothing.
Fix: create `apps/web-dashboard/src/map/layers/v2xLinks.ts` — a `LineLayer` connecting
each vehicle to RSUs within `rsu.coverage_m` using haversine from `utils/geo.ts`.
Wire into `MapView.tsx` with `showV2XLinks` guard.

### 4.3 Trajectory layer — toggle exists, layer does not
`showTrajectories` has the same problem as V2X links. No `createTrajectoriesLayer()`
exists. The trajectory toggle does nothing.
Fix: `apps/web-dashboard/src/map/layers/trajectories.ts` — a `PathLayer` projecting
each vehicle's heading + speed for the next 3 s (3 waypoints at 1 s intervals).
Wire into `MapView.tsx` with `showTrajectories` guard.

### 4.4 Traffic signal positions are random — layer is broken
`infrastructure.ts` line ~51: `getPosition: () => [77.5946 + Math.random() * 0.03, ...]`
Signal dots jump to a new random position every render frame. The fix requires:
- Adding `position?: { lat: number; lon: number }` to `TrafficSignalState` in
  `apps/web-dashboard/src/types/canonical.ts`
- Populating position from the backend's `InfrastructureState.position`
- Removing the `Math.random()` line from `infrastructure.ts`

### 4.5 Accept-reroute AlertPanel action missing
`POST /v1/world-state/reroute` now exists (Amritha, round_2). The frontend
`AlertPanel.tsx` has no "Accept Reroute" button or call. For CRITICAL/HIGH alerts
the panel should offer a reroute action that:
- Calls `POST /v1/world-state/reroute` with the actor's current position
- Renders the returned `route_geometry` waypoints as a `PathLayer` on the map
- Dismisses the alert from the panel for `affected_actor_ids`

### 4.6 False-positive / missed-detection evaluation not run against real data
`tests/safety/evaluation/` has `test_false_positives.py` and `test_missed_detections.py`
but they run against the fixture player's synthetic data. No evaluation has been run
against a real SUMO scenario replay. Policy thresholds in `packages/safety_policies/`
have not been validated for Indian urban traffic patterns (high auto-rickshaw/motorcycle
density, frequent pedestrian incursions). Run the evaluator against
`services/scenario-service/fixtures/bangalore_morning_rush.json` and document
false-positive rates per detector.

---

## Section 5 — Ali: Control Center UX, Driver Console, Replay, Docs

Owner: **Ali** (Control Center, Driver Console, Scenario Studio, live-map UX, E2E verification)

### 5.1 Driver Console backend commands not wired
`DriverConsole.tsx` exists but sends no API calls. Planned driver commands:
- `POST /v1/actors/{id}/command` — speed override, route change, stop
- `POST /v1/signals/{id}/command` — signal phase override by operator
These map to `SimulationAdapter.apply_vehicle_command()` and `apply_signal_command()`
which exist in the protocol but are not exposed as gateway endpoints.

### 5.2 Replay View has no backend
`ReplayView.tsx` exists with a scrubber UI. No backend serves recorded events.
This is blocked on Amritha's 3.5 (persist events during run). Once that lands:
wire `ReplayView.tsx` to `GET /v1/scenarios/{id}/runs/{run_id}/events` and drive
the world-store with timestamped playback at the scrubber position.

### 5.3 Scenario Studio actor placement — map click not implemented
In `ScenarioStudio.tsx`, the "Add Actor" button creates an actor at a hardcoded
`{lat: 12.9716, lon: 77.5946}`. Actors should be placed by clicking on the map.
Fix: on "Add Actor", enter placement mode; on MapView click, read the click lat/lon
and add the actor at that position. The `onEntityClick` prop plumbing is already
in MapView; add a sibling `onMapClick` prop.

### 5.4 `docs/` directory missing entirely
AGENTS.md pre-push checklist: *"Breaking contract changes are documented with a
migration note or ADR."* No `docs/` directory exists. Missing:
- `docs/adr/` — Architecture Decision Records (e.g. why two VehicleState schemas,
  why haversine over PostGIS for V2X range, why NATS over Kafka)
- `docs/api.md` — canonical endpoint reference
- `docs/runbook.md` — how to start the full stack locally and in production

### 5.5 `make typecheck` has unresolved strict-MyPy errors
The original HANDOFF.md noted: *"existing baseline still reports unrelated strict-MyPy
errors in Agent 2 services."* These were never fixed. Running `make typecheck` currently
exits non-zero. CI fails the `mypy` step silently (it's not blocking merge).
Fix: resolve all mypy errors in `services/hazards`, `services/trust`, `services/alerts`,
`services/gateway`, then set `strict = true` for those paths in `pyproject.toml` and
enable the typecheck job as a required CI check.
