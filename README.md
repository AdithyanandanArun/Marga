# MARGA

> **MARGA turns every connected vehicle into an intelligent edge node, using direct V2X communication, adaptive traffic control and cooperative routing to make Indian roads safer and more efficient even under unreliable connectivity and mixed traffic conditions.**

**MARGA** is an edge-first V2X intelligence platform designed for Indian road conditions. A lightweight MARGA runtime is intended to run on a vehicle's ECU/OBU-class compute, using local GNSS, IMU and vehicle-telemetry inputs. Equipped cars, scooters, buses and roadside units communicate directly over C-V2X PC5, allowing time-critical safety functions to continue when internet connectivity is unavailable.

> *Marga* means “path” or “way.” The project focuses on making those paths safer and more efficient under real Indian-road conditions.

## The idea

Conventional connected-vehicle systems often assume reliable cellular coverage, accurate GPS, and predictable traffic. Indian roads cannot make those assumptions: cars, bikes, autos, buses, pedestrians and hazards share constrained roads; connectivity is intermittent; and congestion changes quickly.

Marga is designed as a software-defined resilience layer:

- A lightweight **edge runtime** runs alongside a vehicle ECU/OBU and publishes canonical vehicle state, local risk evidence, and PC5 messages.
- Each participating vehicle acts as a **PC5/C-V2X edge node** for direct nearby discovery and safety delivery. When the internet is unavailable, local warnings still use the PC5 path.
- A compatible **PC5-enabled traffic-light/RSU module** receives local road state and signal-control decisions. It can adjust signal timing without requiring every decision to travel through the cloud.
- The backend builds a **live mobility graph** from vehicles, pedestrians, signals and hazards: density, queue, speed, capacity, confidence and downstream congestion are all explicit.
- A **hybrid control strategy** uses RL for adaptive signal decisions and graph-based routing for feasible, explainable vehicle redistribution. A safety controller validates every signal action and routing never ignores capacity, closures, hazards or uncertainty.

Immediate collision-risk warnings remain on the vehicle edge, while RSU-class edge nodes aggregate nearby observations into the live mobility graph. MARGA is VRU-aware: two-wheelers and pedestrians receive greater consequence weighting during local risk prioritisation.

This is deliberately not an autonomous-driving system. Marga provides confidence-aware warnings and infrastructure coordination; drivers retain control.

## Why hybrid RL + graph optimization?

Running an unconstrained end-to-end RL model for every vehicle route is computationally expensive, difficult to explain, and unsafe under sparse or noisy observations. Marga separates the jobs:

| Layer | Responsibility |
| --- | --- |
| RL signal policy | Proposes discrete, adaptive signal actions such as hold, extend green, or advance to the next safe phase. |
| Signal safety controller | Enforces minimum green, amber/all-red, conflicting-movement, and pedestrian-clearance rules. |
| Mobility graph | Represents live road load, queues, speed, hazards, capacity and GPS confidence. |
| Cooperative routing | Uses graph costs and capacity limits to distribute diversion demand across viable alternatives instead of sending every vehicle to one shortcut. |
| PC5 edge safety | Delivers nearby collision/VRU warnings locally even if cloud connectivity is degraded. |

The result is practical edge deployment: learning is used where adaptation is valuable, while deterministic safety and route-feasibility checks remain inspectable.

## What the current prototype demonstrates

The web simulation and Control Center use the same canonical telemetry path.

1. A connected junction district contains a signalised hub, roundabout, railway crossing and T-junction.
2. Mixed road users generate live actor, pedestrian, signal, risk and graph telemetry.
3. The mobility graph detects queue/density changes; the signal-control service can apply safety-approved phase changes.
4. Collision and VRU risk outputs carry confidence and evidence; PC5 remains the intended local delivery path when the cloud is unavailable.
5. Current Events displays the latest verified conflicts, reported blockages/collisions, reroute suggestions and traffic-signal timing changes.

The routing and signal-control layers are separate by design: **RL controls signal timing; congestion-aware graph routing selects and validates diversions.**

## Architecture

```text
Vehicle ECU / OBU                 Traffic-light / RSU module
  ActorState + local risk             Signal state + PC5 node
          │                                    │
          ├──── PC5 / direct local V2X ────────┤
          │                                    │
          └──────── canonical telemetry ───────┘
                            │
                     Gateway + world state
                            │
                     Live mobility graph
              ┌─────────────┼─────────────┐
              │             │             │
          Risk / V2X    RL signals   Cooperative routing
              │             │             │
              └─────────────┴─────────────┘
                            │
              Control Center + Driver Console
```

Adapters isolate the core from a simulator, a vehicle ECU, phone, OBU, RSU or future C-V2X hardware. SUMO/OSM remain useful for repeatable testing, but the core services consume canonical contracts rather than simulator-specific types. Once compatible C-V2X hardware is available, software-defined safety logic, routing strategies and adaptive models can be delivered through OTA updates rather than requiring major infrastructure replacement.

## Repository layout

```text
apps/web-dashboard/       Control Center and Junction Simulator
packages/schemas/         Canonical actor, risk, signal and graph contracts
services/gateway/         API gateway, world state, live streams, signal bridge
services/mobility_graph/  Confidence-aware road-edge and intersection state
services/routing/         Edge costs, A*/Dijkstra, cooperative rerouting
services/signal-rl/       Adaptive signal policy and safety controller
services/edge_v2x/        Edge nodes, local neighbour discovery and PC5 transport
services/risk/            Trajectory/collision risk evaluation
tests/                    Unit, contract and integration coverage
```

## Run locally

Install Python dependencies (including local service packages):

```bash
make install
```

Start the gateway:

```bash
.venv/bin/uvicorn services.gateway.app:app --host 127.0.0.1 --port 8000
```

In another terminal, start the dashboard:

```bash
npm --prefix apps/web-dashboard install
npm --prefix apps/web-dashboard run dev -- --host 0.0.0.0
```

Open `http://127.0.0.1:3000`. The dashboard starts the shared in-browser simulation adapter; the Control Center renders only data returned through the gateway stream.

## Key contracts and invariants

- Every actor, signal, hazard and graph update is timestamped, source-tagged and versioned.
- Position uncertainty, confidence, evidence and provenance are preserved in safety decisions.
- PC5/local delivery is not silently replaced with a fabricated cloud path when connectivity drops.
- The frontend displays canonical backend state; it does not invent safety alerts.
- Signal actions pass safety constraints before application.
- Routing considers travel time, congestion, hazards, uncertainty, closures and remaining capacity.

## Verification

```bash
make lint
make typecheck
make test
npm --prefix apps/web-dashboard run typecheck
npm --prefix apps/web-dashboard run build
```

## Scope and limitations

This hackathon prototype simulates PC5 transport and road participants; it is not RF-certified C-V2X hardware validation. The ECU/OBU and traffic-light modules are software integration targets with transport-neutral interfaces, so a real C-V2X PC5 implementation can replace the simulated transport without rewriting safety or graph logic. OTA fleet-management infrastructure itself is outside the current prototype.

Marga does not claim autonomous vehicle control, perfect prediction of human intent, or city-scale production traffic optimization.
