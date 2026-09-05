import type { VehicleState, TrafficSignalState, ActorType } from '../types/canonical';
import { samplePath, type SampledPath } from './geometry';
import type { JunctionDefinition, RouteDef } from './junctionDefs';

const SPAWNABLE_TYPES: ActorType[] = ['CAR', 'BIKE', 'AUTO', 'BUS', 'TRUCK'];

interface SpeedProfile {
  cruiseMin: number;
  cruiseMax: number;
  burstMax: number;
  accel: number;
  brakeDecel: number;
  weave: number;
}

// India urban speed ranges (m/s) with real variance — a scooter darting between
// lanes behaves nothing like a loaded truck easing off, so each type gets its
// own accel/brake feel rather than one shared curve.
const SPEED_PROFILE: Record<ActorType, SpeedProfile> = {
  CAR: { cruiseMin: 5, cruiseMax: 13, burstMax: 19, accel: 3.2, brakeDecel: 4.6, weave: 0 },
  BIKE: { cruiseMin: 3, cruiseMax: 11, burstMax: 17, accel: 3.8, brakeDecel: 5.2, weave: 0.9 },
  AUTO: { cruiseMin: 3, cruiseMax: 9, burstMax: 12, accel: 2.4, brakeDecel: 3.6, weave: 0.4 },
  BUS: { cruiseMin: 4, cruiseMax: 10, burstMax: 12, accel: 1.3, brakeDecel: 2.4, weave: 0 },
  TRUCK: { cruiseMin: 3, cruiseMax: 9, burstMax: 11, accel: 1.1, brakeDecel: 2.2, weave: 0 },
  AMBULANCE: { cruiseMin: 8, cruiseMax: 16, burstMax: 22, accel: 3.6, brakeDecel: 4.2, weave: 0 },
  OTHER: { cruiseMin: 4, cruiseMax: 10, burstMax: 14, accel: 2.5, brakeDecel: 3.5, weave: 0.2 },
};

type SimState = 'CRUISE' | 'BURST' | 'BRAKE' | 'STOPPED';

interface SimVehicle {
  id: string;
  actorType: ActorType;
  route: RouteDef;
  sampled: SampledPath;
  progress: number;
  speed: number;
  targetSpeed: number;
  state: SimState;
  nextDecisionAt: number;
  weavePhase: number;
}

export interface EngineOptions {
  junction: JunctionDefinition;
  vehicleCount: number;
  chaos: number;
}

function randRange(min: number, max: number): number {
  return min + Math.random() * (max - min);
}

function pick<T>(arr: T[]): T {
  return arr[Math.floor(Math.random() * arr.length)];
}

/** Self-contained, purely client-local traffic simulation for one junction
 * geometry. Never touches worldStore / WorldStream — its output is handed to
 * the same deck.gl actor/infra layer builders the live dashboard uses, but
 * the vehicles and signals here are synthetic and stay inside this module. */
export class JunctionSimEngine {
  private junction: JunctionDefinition;
  private vehicles: SimVehicle[] = [];
  private chaos: number;
  private clockMs = 0;
  private nextId = 0;

  constructor(opts: EngineOptions) {
    this.junction = opts.junction;
    this.chaos = opts.chaos;
    this.setVehicleCount(opts.vehicleCount);
  }

  setChaos(chaos: number): void {
    this.chaos = chaos;
  }

  setJunction(junction: JunctionDefinition, vehicleCount: number): void {
    this.junction = junction;
    this.vehicles = [];
    this.clockMs = 0;
    this.setVehicleCount(vehicleCount);
  }

  setVehicleCount(count: number): void {
    while (this.vehicles.length < count) this.vehicles.push(this.spawnVehicle(Math.random()));
    if (this.vehicles.length > count) this.vehicles.length = count;
  }

  private spawnVehicle(progress: number): SimVehicle {
    const route = pick(this.junction.routes);
    const actorType = pick(SPAWNABLE_TYPES);
    const profile = SPEED_PROFILE[actorType];
    return {
      id: `sim-${this.nextId++}`,
      actorType,
      route,
      sampled: samplePath(route.path, this.junction.center[1]),
      progress,
      speed: randRange(profile.cruiseMin, profile.cruiseMax),
      targetSpeed: randRange(profile.cruiseMin, profile.cruiseMax),
      state: 'CRUISE',
      nextDecisionAt: 0,
      weavePhase: Math.random() * Math.PI * 2,
    };
  }

  private signalPhase(): 'A' | 'B' | 'NONE' {
    const sig = this.junction.signal;
    if (!sig) return 'A';
    const cycle = sig.greenMs * 2 + sig.allRedMs * 2;
    const t = this.clockMs % cycle;
    if (t < sig.greenMs) return 'A';
    if (t < sig.greenMs + sig.allRedMs) return 'NONE';
    if (t < sig.greenMs * 2 + sig.allRedMs) return 'B';
    return 'NONE';
  }

  private gateOpen(): boolean {
    const gate = this.junction.gate;
    if (!gate) return true;
    const cycle = gate.openMs + gate.closedMs;
    return this.clockMs % cycle < gate.openMs;
  }

  private mustHold(v: SimVehicle): boolean {
    if (v.route.control === 'NONE' || v.route.stopLineFraction === null) return false;
    if (v.progress > v.route.stopLineFraction + 0.015) return false;
    if (v.route.control === 'GATE') return !this.gateOpen();
    const phase = this.signalPhase();
    if (v.route.control === 'SIGNAL_GROUP_A') return phase !== 'A';
    if (v.route.control === 'SIGNAL_GROUP_B') return phase !== 'B';
    return false;
  }

  tick(dtMs: number): { vehicles: VehicleState[]; signals: TrafficSignalState[] } {
    this.clockMs += dtMs;
    const dt = Math.min(0.5, dtMs / 1000);
    const now = this.clockMs;

    for (const v of this.vehicles) {
      const profile = SPEED_PROFILE[v.actorType];

      if (this.mustHold(v)) {
        v.state = 'STOPPED';
        v.targetSpeed = 0;
      } else if (now >= v.nextDecisionAt) {
        const roll = Math.random();
        const burstChance = 0.1 + this.chaos * 0.3;
        const brakeChance = 0.08 + this.chaos * 0.24;
        if (roll < burstChance) {
          v.state = 'BURST';
          v.targetSpeed = randRange(profile.cruiseMax * 0.85, profile.burstMax);
          v.nextDecisionAt = now + randRange(700, 2000);
        } else if (roll < burstChance + brakeChance) {
          const fullStop = Math.random() < 0.25 + this.chaos * 0.35;
          v.state = fullStop ? 'STOPPED' : 'BRAKE';
          v.targetSpeed = fullStop ? 0 : randRange(0, profile.cruiseMin * 0.7);
          v.nextDecisionAt = now + randRange(500, 1600);
        } else {
          v.state = 'CRUISE';
          v.targetSpeed = randRange(profile.cruiseMin, profile.cruiseMax);
          v.nextDecisionAt = now + randRange(1400, 3800);
        }
      }

      const accel = v.speed < v.targetSpeed ? profile.accel : profile.brakeDecel;
      const maxDelta = accel * dt;
      const diff = v.targetSpeed - v.speed;
      v.speed = Math.max(0, v.speed + Math.max(-maxDelta, Math.min(maxDelta, diff)));

      const fracDelta = v.sampled.totalLength > 0 ? (v.speed * dt) / v.sampled.totalLength : 0;
      v.progress += fracDelta;
      v.weavePhase += dt;

      if (v.progress >= 1) {
        Object.assign(v, this.spawnVehicle(0), { id: v.id });
      }
    }

    const nowIso = new Date().toISOString();
    const vehicles: VehicleState[] = this.vehicles.map((v) => {
      const pos = v.sampled.pointAt(v.progress);
      const heading = v.sampled.headingAt(v.progress);
      const profile = SPEED_PROFILE[v.actorType];
      const weaveM = profile.weave ? Math.sin(v.weavePhase * 2.2) * profile.weave : 0;
      return {
        schema_version: '1.0',
        actor_id: v.id,
        actor_type: v.actorType,
        ts: nowIso,
        position: { lat: pos[1] + weaveM / 111_320, lon: pos[0] },
        position_uncertainty_m: 1.5,
        speed_mps: v.speed,
        acceleration_mps2: v.state === 'BURST' ? profile.accel : v.state === 'BRAKE' || v.state === 'STOPPED' ? -profile.brakeDecel : 0,
        heading_deg: heading,
        yaw_rate_dps: 0,
        source: 'SIMULATION',
        capabilities: ['V2X_BASIC'],
        lifecycle: v.state === 'STOPPED' ? 'DEGRADED' : 'ACTIVE',
      };
    });

    const signals: TrafficSignalState[] = [];
    if (this.junction.signal) {
      const phase = this.signalPhase();
      const sig = this.junction.signal;
      signals.push({
        signal_id: 'sim-signal-a',
        junction_id: 'sim-junction',
        ts: nowIso,
        phases: [{ movement_id: 'A', state: phase === 'A' ? 'GREEN' : phase === 'NONE' ? 'AMBER' : 'RED' }],
        controller_mode: 'FIXED',
        source: 'SIMULATION',
        confidence: 1,
        position: { lat: sig.groupA[1], lon: sig.groupA[0] },
      });
      signals.push({
        signal_id: 'sim-signal-b',
        junction_id: 'sim-junction',
        ts: nowIso,
        phases: [{ movement_id: 'B', state: phase === 'B' ? 'GREEN' : phase === 'NONE' ? 'AMBER' : 'RED' }],
        controller_mode: 'FIXED',
        source: 'SIMULATION',
        confidence: 1,
        position: { lat: sig.groupB[1], lon: sig.groupB[0] },
      });
    }
    if (this.junction.gate) {
      const open = this.gateOpen();
      const gate = this.junction.gate;
      signals.push({
        signal_id: 'sim-gate',
        junction_id: 'sim-crossing',
        ts: nowIso,
        phases: [{ movement_id: 'ROAD', state: open ? 'GREEN' : 'RED' }],
        controller_mode: 'FIXED',
        source: 'SIMULATION',
        confidence: 1,
        position: { lat: gate.position[1], lon: gate.position[0] },
      });
    }

    return { vehicles, signals };
  }
}
