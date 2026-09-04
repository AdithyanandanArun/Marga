# Round 2 — Issue Fixes

All 10 issues from the first run, root-caused and assigned.

---

## Amritha — Simulation & World Systems

### 1. Fix WebSocket URL mismatch (frontend never gets real data)

**File:** `apps/web-dashboard/src/net/worldStream.ts` line 11

Frontend connects to `/v1/stream/world` — that route does not exist.
Backend serves `/v1/world-state/stream`.

```ts
// change this line:
worldUrl: config.worldUrl ?? `${wsBase}/v1/stream/world`,
// to:
worldUrl: config.worldUrl ?? `${wsBase}/v1/world-state/stream`,
```

### 2. Remove unconditional fixture player — make it a demo-mode fallback only

**File:** `apps/web-dashboard/src/map/MapView.tsx` lines ~82–84

The `FixturePlayer` starts on every mount regardless of whether the real
WebSocket is connected. This means fixture-generated fake vehicles overwrite
any real actor data coming from the backend.

```ts
// Replace this:
const fixture = new FixturePlayer();
fixture.start(60, 500);
fixtureRef.current = fixture;

// With: only start fixture when WS has not connected after 3 s
let fixtureStarted = false;
const demoFallback = setTimeout(() => {
  if (!wsConnected) {
    const fixture = new FixturePlayer();
    fixture.start(20, 500);   // 20 vehicles, not 60
    fixtureRef.current = fixture;
    fixtureStarted = true;
  }
}, 3000);
```

Track `wsConnected` via `WorldStream.onConnectionChange` callback (already
wired in `Dashboard.tsx`) and cancel `demoFallback` when the WS connects.

### 3. Fix simulation tick speed — vehicles teleporting

**File:** `apps/web-dashboard/src/net/fixtures.ts` `FixturePlayer.tick()`

`dt = 0.5` with speeds up to 30 m/s → 15 m per tick → vehicles jump.
India urban speeds: autos ~8 m/s, cars ~14 m/s, buses ~11 m/s, bikes ~6 m/s.

```ts
private tick(): void {
  this.tickCount++;
  const now = new Date().toISOString();
  const dt = 0.1;   // was 0.5 — match SUMO's 100 ms step

  for (const v of this.vehicles) {
    // clamp speed per type
    const maxSpeed: Record<string, number> = {
      CAR: 14, BUS: 11, TRUCK: 10, AUTO: 8, BIKE: 6,
    };
    const cap = maxSpeed[v.actor_type] ?? 12;
    v.speed_mps = Math.max(0, Math.min(cap, v.speed_mps + (Math.random() - 0.5) * 1));
    // ... rest of movement
  }
}
```

Also change `fixture.start(20, 100)` — 100 ms interval to match real adapter Hz.

**File:** `services/simulation-adapter/runner.py` `SimulationRunner.__init__`

Default `tick_hz=10.0` is correct. Make sure the scenario service does not
override this with a different value. Add a `max_speed_multiplier = 4.0`
guard in `services/scenario-service/app/time_control.py`:

```python
MAX_SPEED_MULTIPLIER = 4.0   # never run faster than 4× real time

def set_speed(self, multiplier: float) -> None:
    self._speed_multiplier = max(0.0, min(MAX_SPEED_MULTIPLIER, multiplier))
```

### 4. Fix actor type classification in the normalizer

**File:** `services/simulation-adapter/normalizer.py`

SUMO pedestrian types must produce `PedestrianState`, not `VehicleState`.
Animals must produce `DynamicActorObservation`. Currently everything goes
through `normalize_vehicle_state`.

Add a dispatcher in `SumoNormalizer`:

```python
PEDESTRIAN_TYPE_IDS = {"pedestrian", "ped", "person"}
ANIMAL_TYPE_IDS = {"cow", "dog", "goat", "cattle", "animal"}

def normalize_actor(self, vehicle_id, raw, timestamp, scenario_run_id, source):
    type_id = raw.get("type_id", "").lower()
    if type_id in PEDESTRIAN_TYPE_IDS:
        return self.normalize_pedestrian_state(vehicle_id, raw, timestamp, scenario_run_id, source)
    if type_id in ANIMAL_TYPE_IDS:
        return self.normalize_dynamic_actor(vehicle_id, raw, timestamp, scenario_run_id, source)
    return self.normalize_vehicle_state(vehicle_id, raw, timestamp, scenario_run_id, source)
```

Update `runner.py` `_tick()` to call `normalize_actor` instead of always
calling `normalize_vehicle_state` so the canonical event type is correct
before it reaches the bridge and the detectors.

### 5. V2X range enforcement — kill km-away connections

**File:** `services/simulation-adapter/base.py` `AdapterConfig`

```python
v2x_range_m: float = 300.0   # RSU/V2V comms capped at 300 m (India DSRC)
```

**File:** `services/integration/canonical_bridge.py`

Add a `range_filter(actor_a, actor_b, range_m)` helper using haversine distance.
The bridge or runner must drop `actor.state.updated` events where the reporting
source (RSU) is > `v2x_range_m` from the actor. This prevents the gateway from
ever seeing an actor that is physically unreachable by the local RSU network.

### 6. Backend rerouting endpoint

**File:** `services/gateway/world_state.py` — add to the existing router:

```python
class RerouteRequest(BaseModel):
    actor_id: str
    origin: dict          # {"lat": float, "lon": float}
    destination: dict     # {"lat": float, "lon": float}
    avoid_segment_ids: list[str] = []

class RerouteResponse(BaseModel):
    actor_id: str
    route_geometry: list[dict]   # list of {"lat", "lon"} waypoints
    avoidance_reason: str
    estimated_delay_s: float
    resolved_alert_ids: list[str]

@router.post("/reroute", response_model=RerouteResponse)
async def reroute(req: RerouteRequest) -> RerouteResponse:
    """Suggest an alternate route avoiding active road events / closures.
    Marks related CRITICAL alerts as RESOLVED once route is accepted.
    """
    ...
```

The implementation can be a simple bearing-based waypoint detour for now —
the key is the API contract exists so the frontend can call it and receive
resolved alert IDs that it can then dismiss from the alert panel.

### 7. Auto-resolve critical alerts after reroute accepted

In the reroute endpoint above, after computing the detour:
1. Query the in-memory alert store (or call `GET /v1/alerts?actor_id=req.actor_id&state=ACTIVE`)
2. For any `CRITICAL` or `HIGH` alert whose `affected_actor_ids` includes the rerouted actor,
   call `PATCH /v1/alerts/{alert_id}` with `{"state": "RESOLVED", "resolution_reason": "reroute_accepted"}`
3. Return the list of resolved alert IDs in `RerouteResponse.resolved_alert_ids`

---

## Hrishi — Safety, Frontend, Alerts

### 8. Fix layer toggles — V2X links and trajectories do nothing

**File:** `apps/web-dashboard/src/map/MapView.tsx`

`showV2XLinks` and `showTrajectories` are read from the store but never
passed to any layer builder. Add them:

```ts
const showV2XLinks = useUIStore((s) => s.showV2XLinks);
const showTrajectories = useUIStore((s) => s.showTrajectories);

// In updateLayers():
...(showV2XLinks ? createV2XLinksLayer(Array.from(vehicles.values()), Array.from(rsus.values())) : []),
...(showTrajectories ? createTrajectoriesLayer(Array.from(vehicles.values()), zoom) : []),
```

**File:** `apps/web-dashboard/src/map/layers/infrastructure.ts`

Create `createV2XLinksLayer(vehicles, rsus)` — a `LineLayer` connecting each
vehicle to RSUs within `rsu.coverage_m` distance only. Use haversine from
`utils/geo.ts` to filter.

**File:** `apps/web-dashboard/src/map/layers/actors.ts` (new file alongside)

Create `createTrajectoriesLayer(vehicles, zoom)` — a `PathLayer` that draws
a short projected path ahead of each vehicle using `heading_deg` + `speed_mps`
for the next 3 seconds.

### 9. Fix signal layer — positions are hardcoded random

**File:** `apps/web-dashboard/src/map/layers/infrastructure.ts` lines 47–55

```ts
// BROKEN:
getPosition: () => [77.5946 + Math.random() * 0.03 - 0.015, ...]

// Fix: TrafficSignalState needs a position field. Add to canonical.ts:
// position?: { lat: number; lon: number }
// Then in the layer:
getPosition: (d: TrafficSignalState) => d.position
  ? [d.position.lon, d.position.lat]
  : [0, 0],   // hide if no position
```

Also add `position?: { lat: number; lon: number }` to the `TrafficSignalState`
type in `apps/web-dashboard/src/types/canonical.ts` and populate it in the
backend's `SignalState` schema.

### 10. Fix map clutter — reduce noise, add zoom LOD

**File:** `apps/web-dashboard/src/net/fixtures.ts`

Reduce initial fixture counts to realistic Bangalore intersection numbers:
```ts
this.vehicles = Array.from({ length: 20 }, (_, i) => generateVehicle(i));
const pedestrians = Array.from({ length: 5 }, ...);
const hazards = Array.from({ length: 3 }, ...);
const dynamicActors = Array.from({ length: 2 }, ...);
```

**File:** `apps/web-dashboard/src/map/layers/actors.ts`

Hide pedestrians and animals below zoom 15 (currently 13):
```ts
if (zoom > 15) { /* pedestrian layer */ }
if (zoom > 15 && dynamicActors.length > 0) { /* animal layer */ }
```

Add a visual label for actor type in the tooltip so users can distinguish
auto/bus/truck without needing different shapes at low zoom.

### 11. Wire "Run Scenario" button in Scenario Studio

**File:** `apps/web-dashboard/src/scenario/ScenarioStudio.tsx`

The Run button has no `onClick`. Wire it to the scenario service API:

```ts
const [runId, setRunId] = useState<string | null>(null);
const [running, setRunning] = useState(false);

const runScenario = async () => {
  // 1. Save scenario first to get an ID
  const createRes = await fetch('/v1/scenarios', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ...scenario, scenario_id: undefined }),
  });
  if (!createRes.ok) { alert('Failed to save scenario'); return; }
  const created = await createRes.json();

  // 2. Start a run
  const runRes = await fetch(`/v1/scenarios/${created.scenario_id}/runs`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ speed_multiplier: 1.0 }),
  });
  if (!runRes.ok) { alert('Failed to start run'); return; }
  const run = await runRes.json();
  setRunId(run.run_id);
  setRunning(true);
};

// Replace dead button with:
<button style={studioStyles.runBtn} onClick={runScenario} disabled={running}>
  <Play size={16} /> {running ? `Running (${runId?.slice(0, 8)})` : 'Run Scenario'}
</button>
```

Also add a Stop button that calls `DELETE /v1/scenarios/{id}/runs/{run_id}`.

### 12. Add rerouting reasoning panel and accept-reroute action

**File:** `apps/web-dashboard/src/components/AlertPanel.tsx`

For CRITICAL/HIGH alerts, add an "Accept Reroute" button that calls
`POST /v1/world-state/reroute` with the affected actor's current position
and clears the alert from the panel when `resolved_alert_ids` comes back.

```ts
const acceptReroute = async (alert: Alert) => {
  const actor = vehicles.get(alert.affected_actor_ids[0]);
  if (!actor) return;
  const res = await fetch('/v1/world-state/reroute', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      actor_id: actor.actor_id,
      origin: actor.position,
      destination: actor.position,   // dashboard can let user pick; default self
    }),
  });
  const body = await res.json();
  for (const id of body.resolved_alert_ids) {
    alertStore.removeAlert(id);
  }
};
```

**New file:** `apps/web-dashboard/src/map/layers/reroute.ts`

A `PathLayer` that renders the suggested detour route on the map when a
reroute response is active. Store the active route in zustand uiStore.

---

## Summary

| # | Issue | Owner | File(s) |
|---|-------|-------|---------|
| 1 | WS URL mismatch — frontend gets no real data | Amritha | `worldStream.ts:11` |
| 2 | Fixture player always on — overwrites real data | Amritha | `MapView.tsx:82` |
| 3 | Vehicles teleporting — tick dt too large | Amritha | `fixtures.ts`, `time_control.py` |
| 4 | Actor types wrong — pedestrians/animals misclassified | Amritha | `normalizer.py`, `runner.py` |
| 5 | V2X km-away connections — no range cap | Amritha | `base.py`, `canonical_bridge.py` |
| 6 | No rerouting API | Amritha | `gateway/world_state.py` |
| 7 | Alerts not resolving after reroute | Amritha | `gateway/world_state.py` |
| 8 | V2X links + trajectory toggles do nothing | Hrishi | `MapView.tsx`, new layer files |
| 9 | Signals at random positions | Hrishi | `infrastructure.ts:47`, `canonical.ts` |
| 10 | Map too cluttered, pedestrians visible at wrong zoom | Hrishi | `fixtures.ts`, `actors.ts` |
| 11 | Scenario Studio Run button dead | Hrishi | `ScenarioStudio.tsx` |
| 12 | No reroute reasoning layer or alert dismiss action | Hrishi | `AlertPanel.tsx`, new `reroute.ts` layer |
