/**
 * Headless traffic soak — checks the two properties that matter for the
 * movement fix: no vehicle stays stuck, and no two rendered bodies overlap.
 *
 * Uses the real engine and the real `vehicleBody` footprints, so a pass here
 * means the physics agrees with what the map draws.
 *
 * Run: npx esbuild scripts/soak.ts --bundle --platform=node --format=cjs | node
 */
import { buildJunctionNetwork, JunctionNetworkEngine, NETWORK_LAYOUT } from '../src/simulation/networkEngine';
import { bodiesOverlap } from '../src/simulation/vehicleBody';
import type { VehicleState } from '../src/types/canonical';

// The engine draws spawn positions, routes and driver variation from
// Math.random. Comparing two physics changes against an unseeded RNG measures
// noise, so pin it: same seed means the same traffic in every run.
function seedRandom(seed: number): void {
  let state = seed >>> 0;
  Math.random = () => {
    state = (state + 0x6d2b79f5) >>> 0;
    let t = state;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
const SEED = Number(process.env.SOAK_SEED ?? 1);
seedRandom(SEED);

const LAT = 12.9550;
const LON = 77.6200;
const VEHICLES = 28;
const DT_MS = 33;
const SIM_MINUTES = 4;
const TICKS = Math.round((SIM_MINUTES * 60_000) / DT_MS);
// A vehicle is stuck when it covers less than STUCK_DISTANCE_M over a rolling
// STALL_WINDOW seconds. The railway gate remains closed for 30 s by design, so
// this observation window must be longer than that legitimate queue duration.
const STALL_WINDOW_S = 40;
const STALL_WINDOW_TICKS = Math.round((STALL_WINDOW_S * 1000) / DT_MS);
const STUCK_DISTANCE_M = 1.0;

const engine = new JunctionNetworkEngine(buildJunctionNetwork(LAT, LON), VEHICLES, 0.5);

const stoppedTicks = new Map<string, number>();
const distanceHistory = new Map<string, number[]>();
const longestStall = new Map<string, number>();
let worstOverlapPairs = 0;
let overlapTicks = 0;
let sampleOverlap = '';
let totalSpeed = 0;
let speedSamples = 0;
const distanceMoved = new Map<string, number>();

const pose = (v: VehicleState) => ({
  id: v.actor_id,
  type: v.actor_type,
  position: [v.position.lon, v.position.lat] as [number, number],
  heading: v.heading_deg,
});

for (let tick = 0; tick < TICKS; tick++) {
  const frame = engine.tick(DT_MS);

  // --- stuck detection -----------------------------------------------------
  // Displacement-based, not speed-based: a vehicle creeping at 0.1 m/s is
  // resolving a give-way negotiation, not stuck, and a speed threshold
  // wrongly counts it. Stuck means it has genuinely not gone anywhere.
  const live = new Set<string>();
  for (const v of frame.vehicles) {
    live.add(v.actor_id);
    const travelled = (distanceMoved.get(v.actor_id) ?? 0) + (v.speed_mps * DT_MS) / 1000;
    distanceMoved.set(v.actor_id, travelled);
    const history = distanceHistory.get(v.actor_id) ?? [];
    history.push(travelled);
    if (history.length > STALL_WINDOW_TICKS) history.shift();
    distanceHistory.set(v.actor_id, history);
    const movedInWindow = travelled - history[0];
    const stalled = history.length >= STALL_WINDOW_TICKS && movedInWindow < STUCK_DISTANCE_M
      ? (stoppedTicks.get(v.actor_id) ?? STALL_WINDOW_TICKS) + 1
      : 0;
    stoppedTicks.set(v.actor_id, stalled);
    longestStall.set(v.actor_id, Math.max(longestStall.get(v.actor_id) ?? 0, stalled));
    totalSpeed += v.speed_mps;
    speedSamples++;
  }
  for (const id of [...stoppedTicks.keys()]) if (!live.has(id)) { stoppedTicks.delete(id); distanceHistory.delete(id); }

  // --- body overlap detection (rendered footprints, not centre points) ------
  const bodies = frame.vehicles.map(pose);
  let pairs = 0;
  for (let i = 0; i < bodies.length; i++) {
    for (let j = i + 1; j < bodies.length; j++) {
      if (bodiesOverlap(bodies[i], bodies[j], 0)) {
        pairs++;
        if (!sampleOverlap) {
          const a = bodies[i], b = bodies[j];
          sampleOverlap = `${a.id} (${a.type}) x ${b.id} (${b.type})`;
          if (process.env.SOAK_OVERLAP) {
            console.log(`first overlap at tick ${tick} (t=${((tick * DT_MS) / 1000).toFixed(1)}s)`);
            console.log(`  ${a.id} type=${a.type} travelled=${(distanceMoved.get(a.id) ?? 0).toFixed(1)}m heading=${a.heading.toFixed(0)}`);
            console.log(`  ${b.id} type=${b.type} travelled=${(distanceMoved.get(b.id) ?? 0).toFixed(1)}m heading=${b.heading.toFixed(0)}`);
          }
        }
      }
    }
  }
  if (pairs > 0) overlapTicks++;
  worstOverlapPairs = Math.max(worstOverlapPairs, pairs);
}

if (process.env.SOAK_DIAG) {
  // Separate "never moved" (spawn/gridlock) from "moved then stopped".
  const rows = [...longestStall.entries()]
    .map(([id, ticks]) => ({ id, stallS: (ticks * DT_MS) / 1000, moved: distanceMoved.get(id) ?? 0 }))
    .filter((r) => r.stallS > 30)
    .sort((a, b) => b.stallS - a.stallS);
  console.log(`seed=${SEED} stalled>30s: ${rows.length}`);
  for (const r of rows.slice(0, 12)) {
    console.log(`  ${r.id.padEnd(26)} stall=${r.stallS.toFixed(0).padStart(4)}s  travelled=${r.moved.toFixed(1).padStart(7)}m`);
  }
  process.exit(0);
}

if (process.env.SOAK_TERSE) {
  const s = [...longestStall.values()].map((t) => (t * DT_MS) / 1000);
  const worst = s.length ? Math.max(...s) : 0;
  console.log(`seed=${SEED} stalls>30s=${s.filter((x) => x > 30).length} worst=${worst.toFixed(0)}s `
    + `mean=${(totalSpeed / Math.max(1, speedSamples)).toFixed(2)}m/s overlapTicks=${overlapTicks}`);
  process.exit(0);
}

const byJunction = new Map<string, { stalled: number; total: number }>();
for (const [id, ticks] of longestStall) {
  const junction = id.split('-').slice(0, 3).join('-');
  const entry = byJunction.get(junction) ?? { stalled: 0, total: 0 };
  entry.total++;
  if ((ticks * DT_MS) / 1000 > 30) entry.stalled++;
  byJunction.set(junction, entry);
}
console.log('stalls by junction:');
for (const [junction, { stalled, total }] of [...byJunction].sort()) {
  console.log(`  ${junction.padEnd(24)} ${stalled}/${total} stalled >30s`);
}

const stallSeconds = [...longestStall.values()].map((t) => (t * DT_MS) / 1000);
const worstStallS = stallSeconds.length ? Math.max(...stallSeconds) : 0;
const stuckOver30s = stallSeconds.filter((s) => s > 30).length;

console.log(`junctions        : ${NETWORK_LAYOUT.map((j) => j.id).join(', ')}`);
console.log(`simulated        : ${SIM_MINUTES} min at ${VEHICLES} vehicles (${TICKS} ticks)`);
console.log(`mean speed       : ${(totalSpeed / Math.max(1, speedSamples)).toFixed(2)} m/s`);
console.log(`longest stall    : ${worstStallS.toFixed(1)} s`);
console.log(`vehicles >30s    : ${stuckOver30s}`);
console.log(`ticks w/ overlap : ${overlapTicks} / ${TICKS}`);
console.log(`worst overlaps   : ${worstOverlapPairs} pair(s) ${sampleOverlap ? `e.g. ${sampleOverlap}` : ''}`);

const failures: string[] = [];
if (stuckOver30s > 0) failures.push(`${stuckOver30s} vehicle(s) stalled >30 s`);
if (overlapTicks > 0) failures.push(`body overlap on ${overlapTicks} tick(s)`);
console.log(failures.length ? `\nFAIL: ${failures.join('; ')}` : '\nPASS: no permanent stalls, no body overlap');
process.exit(failures.length ? 1 : 0);
