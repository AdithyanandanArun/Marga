# Marga — Final Implementation Blueprint

## Goal

Deliver one real vertical slice:

```text
SUMO road users -> live mobility graph -> RL signals / cooperative routing / edge V2X
-> actual SUMO changes and PC5 safety delivery -> understandable UI
```

The demo must show traffic improving while local safety remains available with internet off.

## Ownership

| Owner | Owns | Must avoid |
| --- | --- | --- |
| Adithyan1 | Canonical contracts and live mobility graph | RL, routing policy, UI |
| Amritha | SUMO signal adapter, RL training/control, signal safety controller | Graph schemas, routing, frontend |
| Adithyan2 | Dynamic edge costs, A*/Dijkstra, cooperative rerouting, SUMO route injection | Signal policy, frontend |
| Hrishi | Edge V2X nodes, simulated PC5, offline delivery, VRU-aware safety | Routing and signal policy |
| Ali | Control Center, Driver Console, Replay, visualization, E2E proof | Core algorithms |

Adithyan1 owns every shared contract. Other agents consume these schemas and never create parallel actor, graph, route, signal, or risk types. Contract changes need migration notes and contract tests.

## Adithyan1 — Live Mobility Graph

Normalize every vehicle, two-wheeler, bus, and pedestrian observation into canonical `ActorState`; map actors to OSM/SUMO edges and lanes; continuously publish road and intersection state.

Each edge provides: vehicle count, density, two-wheeler ratio, average speed, queue length, flow rate, occupancy, capacity ratio, hazards, GPS confidence, downstream congestion, and 5/15/30/60-second rolling windows.

```json
{"edge_id":"edge-103","vehicle_count":28,"density":0.76,"avg_speed_mps":4.8,"queue_length":17,"two_wheeler_ratio":0.42,"capacity_ratio":0.81,"hazard_penalty":0.0,"gps_confidence":0.91}
```

```text
GET /graph/edges/:id
GET /graph/intersections/:id
WS  graph.edge.updated
WS  graph.intersection.updated
```

## Amritha — RL Dynamic Signals

Consume graph state only. State includes per-approach queue, lane density, speed, incoming flow, downstream occupancy, phase/duration, pedestrian demand, and VRU density. Actions are strictly `HOLD`, `EXTEND_GREEN_5`, `EXTEND_GREEN_10`, or `NEXT_PHASE`.

Reward reduces waiting time, queue length, spillback, pedestrian wait, and stops while improving throughput and average speed. Every RL proposal passes a safety controller for minimum green, yellow, all-red, pedestrian clearance, and conflicting movements. Train repeatable SUMO episodes and compare against fixed-time control using wait/trip time, queues, throughput, and stops.

```text
POST /signals/:junction/recommend
POST /signals/:junction/apply
GET  /signals/:junction/state
```

Accepted actions must visibly alter actual SUMO signal phases.

## Adithyan2 — Dynamic Cooperative Routing

Use edge cost = travel time + congestion penalty + hazard penalty + uncertainty penalty + closure penalty. Run A* or Dijkstra. Reroute only for a material ETA improvement or a critical hazard/closure, preventing oscillation.

Cooperatively distribute affected vehicles across compatible routes based on remaining capacity, predicted incoming demand, and ETA; never move all traffic to one shortcut. Inject accepted routes into SUMO and expose old/new ETA plus the reason.

```text
POST /routes/recalculate
GET  /routes/:vehicle
WS   route.changed
```

## Hrishi — Edge V2X, Offline Safety, and VRUs

Each simulated OBU/ECU has actor state, nearby peers, local risk evaluation, message priority, and a transport. Provide a transport-neutral `send`, `receive`, `nearbyNodes`, `linkQuality`, and `transportState` interface with `SimulatedPC5Transport` now and a replaceable real C-V2X transport later.

Internet off must remove cloud delivery but preserve local PC5 safety delivery. Cover intersection, head-on, rear-end, side-swipe, emergency-braking, and VRU conflicts. Prioritize one active risk using collision probability, TTC, uncertainty, consequence, and road-user vulnerability.

```text
WS  v2x.message
WS  risk.created
GET /nodes/:id/neighbours
GET /nodes/:id/connectivity
```

## Ali — Product UI and E2E

The main view shows only the present story: `INTERNET OFF`, `DIRECT V2X ACTIVE`, GPS uncertainty, the live conflict, one active risk, and road density/speed. Roads use load colours. Edge selection shows graph metrics; signal selection shows queues, RL decision, and outcome; a rerouted vehicle shows old/new routes, ETA change, and reason; PC5 links stay local.

Ali owns the real integration proof:

```text
SUMO -> mobility graph -> RL/routing/edge V2X -> SUMO changes -> UI
```

No frontend-generated fake events are acceptable.

## First shared milestone

One deterministic scenario must show:

1. Junction congestion detected by the mobility graph.
2. Safe RL signal change and a visibly reduced queue.
3. Congestion on another road; cooperative rerouting and actual SUMO route changes.
4. Internet disabled with direct PC5 safety delivery still working.
5. A car/scooter conflict producing one early VRU-aware warning.
6. UI evidence understandable in seconds.

All work keeps canonical contracts, confidence/evidence/provenance, replayability, and simulation-reality parity. Resolve conflicts semantically, do not commit credentials or local environments, and never use `Co-authored-by` trailers.
