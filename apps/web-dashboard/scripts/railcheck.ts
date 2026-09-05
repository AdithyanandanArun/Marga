/**
 * Level-crossing safety check.
 *
 * Asserts the rule that matters: while the gate is RED, no vehicle is on the
 * track. Also reports how long a committed vehicle takes to clear the box, so
 * a regression that makes them dawdle is visible rather than silent.
 *
 * Run: npx esbuild scripts/railcheck.ts --bundle --platform=node --format=cjs \
 *        --outfile=/tmp/railcheck.cjs && node /tmp/railcheck.cjs
 */
import { buildJunction } from '../src/simulation/junctionDefs';
import { JunctionSimEngine } from '../src/simulation/vehicleEngine';
import { distanceMeters } from '../src/simulation/geometry';

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

const DT_MS = 33;
const MINUTES = 12;
const TICKS = Math.round((MINUTES * 60_000) / DT_MS);

const junction = buildJunction('RAILWAY_CROSSING', 12.955, 77.62);
const gate = junction.gate!;
const centerLat = junction.center[1];

let violations = 0;
let worstViolationM = 0;
let redTicks = 0;
let occupiedDuringRed = 0;
const clearTimes: number[] = [];

for (const seed of [1,2,3,4,5,6,7,8]) {
  seedRandom(seed);
  const engine = new JunctionSimEngine({
    junction, vehicleCount: 18, chaos: 0.6,
    actorIdPrefix: 'rail', junctionId: 'rail-check',
  });
  // How long each vehicle has been inside the box, to measure clearance time.
  const insideSince = new Map<string, number>();

  for (let tick = 0; tick < TICKS; tick++) {
    const frame = engine.tick(DT_MS);
    const gateSignal = frame.signals.find((s) => s.signal_id.endsWith('-gate'));
    const state = gateSignal?.phases?.[0]?.state;
    const red = state === 'RED';
    if (red) redTicks++;

    let anyInside = false;
    for (const v of frame.vehicles) {
      const d = distanceMeters([v.position.lon, v.position.lat], junction.center, centerLat);
      const inside = d < gate.clearanceRadiusM;
      if (inside) {
        anyInside = true;
        if (!insideSince.has(v.actor_id)) insideSince.set(v.actor_id, tick);
        if (red) {
          violations++;
          worstViolationM = Math.max(worstViolationM, gate.clearanceRadiusM - d);
        }
      } else if (insideSince.has(v.actor_id)) {
        clearTimes.push(((tick - insideSince.get(v.actor_id)!) * DT_MS) / 1000);
        insideSince.delete(v.actor_id);
      }
    }
    if (red && anyInside) occupiedDuringRed++;
  }
}

const worstClear = clearTimes.length ? Math.max(...clearTimes) : 0;
const meanClear = clearTimes.length ? clearTimes.reduce((a, b) => a + b, 0) / clearTimes.length : 0;

console.log(`stop line          : ${gate.clearanceRadiusM.toFixed(1)} m clearance radius`);
console.log(`gate cycle         : ${gate.openMs / 1000}s open, ${gate.warningMs / 1000}s warning, ${gate.closedMs / 1000}s closed`);
console.log(`red ticks          : ${redTicks}`);
console.log(`crossings measured : ${clearTimes.length}`);
console.log(`clearance time     : mean ${meanClear.toFixed(1)}s, worst ${worstClear.toFixed(1)}s`);
console.log(`ticks w/ vehicle on track during RED : ${occupiedDuringRed}`);
console.log(`vehicle-ticks in violation           : ${violations}`);
if (violations) console.log(`deepest intrusion  : ${worstViolationM.toFixed(1)} m inside the box`);

console.log(violations === 0
  ? '\nPASS: track is empty for the whole of every red period'
  : `\nFAIL: track occupied during red on ${occupiedDuringRed} tick(s)`);
process.exit(violations === 0 ? 0 : 1);
