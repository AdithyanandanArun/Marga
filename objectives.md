# Marga — Three-Agent Demo Objectives

This plan is aligned with the product manual and the actual judging story:

> Marga predicts mixed-traffic conflicts and continues delivering explainable
> safety warnings when Indian-road connectivity or GPS quality degrades.

The demo is one deterministic, road-constrained safety loop. The browser must
render backend truth; it must never invent risks, alerts, or metrics.

## Demo acceptance flow

The team is finished when a judge can see this without explanation:

```text
normal road traffic
  → bike/car approach one junction
  → TTC conflict is predicted
  → evidence-bearing warning appears
  → internet becomes unavailable
  → direct V2X keeps the warning alive
  → GPS uncertainty expands
  → fused confidence changes
  → driver accepts a safer action
  → risk resolves and the trace remains inspectable
```

The first screen must show only the road scene, relevant actors, one active
incident, TTC, confidence, GPS quality, network state, evidence, and the next
action. Advanced metrics and service controls belong on secondary screens.

---

## Agent 1 — Judge-facing application and visual truth

Owner: **Agent 1**

Agent 1 owns everything the judge sees in the browser. The UI is a renderer of
canonical backend state, not a simulation engine.

### A1.1 Connect the dashboard to live backend state

- Start `WorldStream` from the dashboard lifecycle.
- Track WebSocket connection state.
- Stop the fixture fallback when the real stream connects.
- Keep fixtures available only through an explicit offline/demo switch.
- Remove browser-side random risk, alert, and metric generation from the normal path.

Acceptance: a backend ingest changes the browser; a browser timer alone cannot
create a safety alert.

### A1.2 Build the focused resilience screen

Replace the control-room-first layout with a focused junction view containing:

- 5–8 road actors maximum;
- road/lane geometry and direction-oriented vehicle markers;
- short motion trails and trajectory projections;
- one highlighted conflict point;
- uncertainty circles;
- network state: `INTERNET`, `DIRECT V2X`, or `OFFLINE`;
- GPS quality and fused confidence;
- one evidence-bearing alert card.

Move health charts, trust details, bulk hazards, and infrastructure diagnostics
behind an Advanced/Inspector view.

### A1.3 Make the warning understandable

The active alert must show:

- plain-language threat;
- affected actors;
- TTC countdown;
- confidence;
- GPS uncertainty;
- evidence sources;
- policy version and trace ID;
- recommended action.

### A1.4 Show connectivity and positioning resilience

- Render the internet-off transition visibly.
- Render direct V2X links only for actors within configured range.
- Expand the uncertainty radius when GPS quality degrades.
- Display confidence changing rather than hiding degraded quality.

### A1.5 Complete the operator action

- Add `Accept Reroute` for critical/high alerts.
- Render returned route geometry.
- Clear only the alerts resolved by the backend.
- Show a short “why this route” explanation.

### A1.6 Agent 1 verification

- Dashboard TypeScript build and typecheck pass.
- Browser E2E test proves backend actor event → visible risk → visible alert.
- No random risk, alert, or metric generation is active in the normal path.
- A presenter can complete the acceptance flow in under two minutes.

---

## Agent 2 — Backend truth, resilience, and explainability

Owner: **Agent 2**

Agent 2 owns the canonical backend pipeline and all state that determines the
meaning of the UI.

### A2.1 Stabilize canonical contracts

- Publish one documented set of REST/WebSocket paths.
- Align README, frontend, and gateway route names.
- Preserve schema version, UTC timestamp, source, trace ID, confidence, and
  uncertainty on every event.
- Add contract tests for vehicle, pedestrian, hazard, connectivity, and position
  quality events.

### A2.2 Make the safety pipeline authoritative

The production path must be:

```text
canonical event
  → world state
  → position fusion
  → trajectory/TTC risk
  → prioritized alert
  → WebSocket delivery
```

- Backend creates all `RiskEvent`s and alerts.
- Frontend never fabricates safety outcomes.
- Risk evidence includes TTC, minimum distance, uncertainty, confidence, and
  policy version.

### A2.3 Implement connectivity degradation

- Add canonical connectivity state transitions.
- Support `FULL`, `DIRECT_ONLY`, `INTERMITTENT`, and `ISOLATED`.
- Preserve safety-critical local delivery in `DIRECT_ONLY` mode.
- Lower confidence or change delivery path when cloud connectivity disappears.
- Expose the active state through the world/metrics stream.

### A2.4 Implement positioning degradation

- Accept position-quality/GPS degradation events.
- Fuse GNSS/RSU/vehicle observations using uncertainty-aware weighting.
- Propagate uncertainty into trajectory confidence, TTC confidence, and alerts.
- Never replace degraded positioning with a precise fabricated coordinate.

### A2.5 Persist and explain incidents

- Persist risk events, alerts, evidence, and decision traces.
- Implement `GET /v1/incidents/{id}/trace` against durable storage.
- Make traces replayable after a gateway restart.
- Add an end-to-end test for trace creation and retrieval.

### A2.6 Implement operator actions and observability

- Complete reroute resolution and return resolved alert IDs.
- Add detector latency, risk count, alert count, confidence, uncertainty, and
  connectivity metrics.
- Replace random dashboard metrics with real counters/histograms.
- Add gateway/safety/scenario services to the correct Compose file.
- Correct the Makefile Compose path.

### A2.7 Agent 2 verification

- Full canonical contract suite passes.
- In-process E2E proves event → risk → alert → WebSocket.
- Connectivity-off warning remains available through direct V2X.
- GPS degradation increases uncertainty and changes confidence.
- `make test` and focused typechecks are green, with unrelated failures tracked.

---

## Amrita — deterministic road scenario and input feeds

Owner: **Amrita**

Amrita owns realistic, repeatable inputs. The scenario must look like traffic on
a road, not random points moving over a map.

### AM.1 Build the Bangalore junction scenario

- Use a real or clearly defined Bangalore road/junction geometry.
- Define lanes/routes and legal movement directions.
- Include one ego car/auto, one bus/bike conflict actor, and a small background
  traffic population.
- Keep all actors on road geometry with lane offsets and smooth acceleration.
- Use a fixed seed and deterministic timestamps for repeatable judging.

### AM.2 Schedule the resilience event sequence

The scenario scheduler must emit canonical events for:

1. normal traffic and good GPS;
2. an approaching mixed-traffic conflict;
3. GPS degradation from approximately ±4 m to ±25 m;
4. internet loss with direct V2X still available;
5. conflict warning and recommended action;
6. reroute/braking resolution;
7. restoration of connectivity and positioning quality.

These are scenario inputs consumed by the real backend, not frontend special
cases or hard-coded alert outcomes.

### AM.3 Integrate simulation with the gateway

- Scenario Studio Run must start the scenario service.
- The simulation must publish canonical actor, infrastructure, connectivity,
  and position-quality events to the gateway.
- The dashboard must receive the resulting world and alert streams.
- Stop/cancel must cleanly terminate the run.

### AM.4 Preserve simulation-reality parity

- Keep SUMO/mock output behind the adapter boundary.
- Validate pedestrian, bike, auto, bus, and car normalization.
- Keep GNSS, OBU, RSU, and phone-GPS adapters on the same canonical contracts.
- Add one parity test showing a simulator feed and real-adapter-shaped feed
  produce equivalent canonical state.

### AM.5 Replay and load validation

- Persist the scenario event sequence with timestamps.
- Serve replay windows by scenario/run ID.
- Run the Bangalore scenario at normal speed and accelerated speed.
- Record event throughput, risk latency, and alert delivery results.

### AM.6 Amrita verification

- Scenario starts from the UI and produces backend events.
- Actors remain on roads throughout the run.
- The same seeded run produces the same event sequence.
- The full demo acceptance flow can be replayed from a clean startup.

---

## Explicitly deferred until the demo loop works

- broad control-center analytics;
- nationwide traffic simulation;
- large animal/hazard catalogs;
- advanced trust dashboards;
- full NATS/Redis optimization beyond what the resilience flow needs;
- nonessential driver commands;
- visual polish that does not clarify the three resilience states.

## Definition of done

The work is not done because the repository contains many services. It is done
when a judge can understand, in five seconds, that Marga:

1. predicts a real mixed-traffic conflict;
2. continues warning when internet connectivity fails;
3. reasons honestly about degraded GPS;
4. explains its evidence and confidence; and
5. helps the road user resolve the danger.
