# Marga

**Marga** is an India-ready, software-defined V2X (vehicle-to-everything) resilience platform. It turns live road-user, infrastructure, and hazard signals into explainable, confidence-aware safety alerts—while remaining useful through unreliable positioning and connectivity.

> The name *Marga* means “path” or “way.” Our goal is safer, more resilient paths on Indian roads.

## Why Marga

Indian roads combine dense mixed traffic, variable road quality, non-connected road users, inconsistent connectivity, and rapidly changing hazards. Marga provides a deployable software core that can:

- ingest vehicle, pedestrian, infrastructure, and road-event data through a common contract;
- estimate position uncertainty and preserve it in every safety decision;
- predict conflicts from trajectories and road geometry;
- fuse corroborating hazard reports, while handling expiry and confidence;
- detect safety situations such as collision risks, wrong-way travel, emergency braking, stalled vehicles, blind intersections, road hazards, and animal/pedestrian conflicts;
- prioritize actionable alerts with evidence, trust, and uncertainty; and
- continue safety-critical messaging through degraded or offline connectivity paths.

SUMO is used to supply realistic test inputs during the hackathon. It is never the product logic: the same core is designed to accept future GNSS, phone, OBU, RSU, and vehicle-telemetry adapters.

## Architecture

```text
OSM / SUMO / future real-world feeds
              |
        Input adapters
              |
      Canonical State API
              |
        World-state bus
   ┌──────────┼──────────┐
 Position   Trust     Hazard fusion
   └──────────┼──────────┘
        Risk / collision engine
              |
       Alert prioritization
      ┌───────┴────────┐
   REST + WebSocket   V2X adapters
      |
 Control Center / Driver Console
```

The backend is the source of truth. The frontend renders canonical world and alert state and sends explicit operator commands; it never invents safety outcomes.

## Technology direction

| Area | Baseline |
| --- | --- |
| Road network | OpenStreetMap |
| Simulation | SUMO via TraCI (libsumo for scale) |
| Services | Python 3.12+ and FastAPI |
| Live APIs | REST, WebSocket, event streams |
| Mapping | MapLibre GL JS + deck.gl |
| Spatial persistence | PostgreSQL + PostGIS |
| Ephemeral state | Redis (where needed) |
| Event transport | Broker-neutral contracts; NATS JetStream or Redpanda/Kafka at scale |
| Observability | OpenTelemetry + Prometheus-compatible metrics |

## Core principles

- **No demo-only logic.** No canned outcomes, fixed coordinates, hard-coded actor IDs, or `if demo_case` branches.
- **Canonical contracts first.** Every adapter normalizes data before it reaches decision logic; core services do not depend on SUMO, frontend, or vendor-specific types.
- **Confidence is mandatory.** Safety outputs carry uncertainty, evidence, provenance, and a policy/version basis.
- **Simulation–reality parity.** Replacing a simulator feed with a real feed must require adapter/configuration changes, not a rewrite of the core.
- **Offline-first safety.** Disrupted cloud connectivity must lower certainty or alter delivery paths—not silently manufacture certainty.
- **Explainable decisions.** Incidents and alerts are persisted/replayable with their contributing evidence and decision trace.

## First vertical slice

Before feature expansion, Marga will prove one real end-to-end flow:

```text
OSM map → SUMO actor → canonical VehicleState → world state
→ position/risk evaluation → evidence-bearing Alert → WebSocket → Control Center
```

This slice must work on an imported OSM network and with arbitrary valid inputs—not only a prepared demo scenario.

## Planned repository layout

```text
apps/             # Web dashboard, driver console, scenario studio
services/         # Gateway, world state, position, risk, hazards, trust, alerts, simulation adapter
packages/         # Canonical schemas, geospatial helpers, V2X protocol
tests/            # Contract, integration, E2E, and performance suites
infra/            # Docker, compose, Kubernetes, observability
tools/            # OSM import, scenario building, load generation
docs/             # Architecture decisions and API documentation
```

## Canonical event flow

All entities and messages will have a `schema_version`, UTC timestamps, source metadata, and stable correlation/trace identifiers. Core event families include:

- `actor.state.updated`
- `infrastructure.signal.updated`
- `hazard.observed` and `hazard.updated`
- `position.estimate.updated`
- `trust.assessment.updated`
- `risk.detected`
- `alert.issued`

Key invariants: speeds are stored in m/s, headings are degrees clockwise from true north (`0–360`), and confidence is always expressed from `0` to `1`.

## API direction

The gateway will expose canonical ingestion and world-state endpoints such as:

```text
POST /v1/ingest/vehicle-state
POST /v1/ingest/pedestrian-state
POST /v1/ingest/hazard-observation
GET  /v1/world/snapshot
GET  /v1/incidents/{id}/trace
WS   /v1/stream/world
WS   /v1/stream/alerts
```

Test-only endpoints will drive fault injection and scenarios so network, GPS, RSU, and actor failures affect the real processing pipeline.

## Roadmap

1. Bootstrap repository, CI, canonical schemas, and contract tests.
2. Import an OSM region and stream SUMO actors/signals into world state.
3. Render live actors and hazards with MapLibre/deck.gl.
4. Build generic trajectory and time-to-collision risk detection.
5. Add position uncertainty, connectivity resilience, hazards, and trust.
6. Expand India-focused safety features: mixed traffic, wrong-way travel, blind intersections, road narrowing, animal/pedestrian risks, and emergency priority.
7. Add deterministic scenario replay, scale testing, containers, and real-adapter stubs.

## Team ownership

| Owner | Focus |
| --- | --- |
| Adithyan — Lead | Architecture, canonical contracts, integration, release decisions |
| Adithyan — Agent 1 | World state, geospatial/position core, trajectories, collision risk, evidence |
| Adithyan — Agent 2 | Hazard fusion, trust/security, offline transport, persistence, observability, CI/deploy |
| Amrita | OSM/SUMO, simulation and world systems, deterministic scenarios, failure injection |
| Hrishi | Safety policies, acceptance evaluation, false-positive/missed-detection analysis |
| Ali | Control Center, Driver Console, Scenario Studio, live-map UX, E2E verification |

## Development status

The repository has been initialized. The next foundation work is to add the service skeleton, canonical schemas, local container environment, and automated contract checks. Until those land, the commands and endpoints above are architecture targets rather than runnable interfaces.

## Contribution rules

- Preserve public contracts; document compatibility changes with a migration note or ADR and update affected tests.
- Treat every pull/integration as a deliberate conflict-resolution task—inspect intent and rerun affected checks.
- Do not add `Co-authored-by` trailers or equivalent co-author tags to commits.
- Finish each change with appropriate tests and a handoff note covering contracts, files, migrations/configuration, test results, limitations, and likely conflict hotspots.

## Non-goals

Marga does not claim hardware/RF C-V2X validation during this hackathon, autonomous vehicle control, perfect prediction of human or animal intent, or nationwide microscopic simulation in a browser.

