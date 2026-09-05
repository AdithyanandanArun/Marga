# Finish This Run

This file records the remaining work from the current movement and performance run. The changes are local and have not been committed or pushed.

## Current objective

Make the junction simulation look like dense but competent Indian traffic:

- vehicles keep moving when the road and signal permit it;
- buses, trucks, cars, autos, and scooters maintain physical clearance;
- overtaking and lane changes never put a vehicle into an occupied or opposing body;
- a genuinely blocked vehicle becomes an explained accident event, affected traffic reroutes, the stale actor leaves, and a replacement enters;
- the Control Center stays responsive while live telemetry is flowing.

## Changes already made in this run

- Added shared oriented body geometry in `apps/web-dashboard/src/simulation/vehicleBody.ts`.
- Reused body dimensions in the actor renderer so the displayed footprint matches physics.
- Added swept movement checks, spawn clearance checks, and safer transfer checks to the junction engine.
- Added predictive conflict yielding and collision-safe lane return logic.
- Removed arbitrary random full stops on open roads.
- Added stalled-vehicle accident evidence, affected-vehicle rerouting, despawn, and replacement spawning.
- Batched vehicle and signal ingestion in the gateway so one simulation frame produces one vehicle delta and one signal delta.
- Rate-limited PC5 observation updates and reduced browser telemetry to 4 Hz.

## Still required before this can be called finished

1. Run the production dashboard build after the final body-geometry edit and open the browser manually.
2. Exercise all four junction types for at least 2–5 minutes at 20–30 vehicles.
3. Capture minimum body-to-body separation from rendered vehicle poses, not centre-point distance. There must be no body overlap during normal traffic, lane changes, transfers, or turns.
4. Verify the screenshots no longer show stacked rectangles at a straight road, stop line, junction, or railway crossing.
5. Verify a red signal creates a spaced queue and does not classify the queue as an accident.
6. Verify an induced, uncontrolled blockage produces exactly one accident hazard with evidence, reroutes vehicles behind it, removes the blocked actor, and restores the configured traffic count.
7. Verify that a rerouted vehicle changes route geometry continuously; no teleporting, route flip-flopping, or identical old/new route may be reported.
8. Verify the Control Center remains responsive with one simulator tab and one Control Center tab. Multiple browser tabs currently act as independent telemetry producers and can still contaminate a shared gateway world; a server-owned simulation or producer lease is still needed for multi-client correctness.
9. Register simulator signal topologies with the RL signal controller and connect applied RL actions to the simulator. This remains an integration gap from the previous browser audit.
10. Fix the routing service so `rerouted: true` cannot be returned when the old and new geometry/ETA are unchanged.

## Validation already observed

- Dashboard TypeScript typecheck passed after the latest body-geometry edit.
- Earlier production builds passed before the final body-geometry edit.
- Earlier backend regression run passed 174 integration and Edge-V2X tests.
- Earlier isolated physics soak showed no centre-distance body overlaps, but that test was insufficient because it did not use vehicle footprints. Repeat it with `vehicleBody.ts` before trusting the result.
- Dashboard lint is currently unavailable because the project has no installed `eslint` binary; resolve the dependency/tooling before using the lint checklist.

## Safe continuation order

```text
typecheck/build
  -> isolated footprint collision soak
  -> browser visual soak on one simulator + one Control Center
  -> accident/despawn/reroute acceptance test
  -> RL topology and routing integration
  -> review git diff
  -> commit without a Co-authored-by trailer
  -> push only after review
```

Do not stage `graphify-out/cache/last_query_stamp` or generated `marga.egg-info` files. Preserve the existing unrelated integration edits in the worktree while reviewing the files listed above.
