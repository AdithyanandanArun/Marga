import type { VehicleState, TrafficSignalState, ActorType } from '../types/canonical';
import { distanceMeters, samplePath, type Point, type SampledPath } from './geometry';
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
  laneOffsetM: number;
  targetLaneOffsetM: number;
}

export interface TransferredVehicle {
  actorId: string;
  actorType: ActorType;
  position: Point;
}

export interface EngineOptions {
  junction: JunctionDefinition;
  vehicleCount: number;
  chaos: number;
  /** Stable namespace supplied by the simulation adapter, not a UI identity. */
  actorIdPrefix?: string;
  junctionId?: string;
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
  private readonly actorIdPrefix: string;
  private readonly junctionId: string;
  private clockMs = 0;
  private nextId = 0;

  constructor(opts: EngineOptions) {
    this.junction = opts.junction;
    this.chaos = opts.chaos;
    this.actorIdPrefix = opts.actorIdPrefix ?? 'sim';
    this.junctionId = opts.junctionId ?? 'sim-junction';
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
    const route = this.chooseRoute(this.junction.routes);
    const actorType = pick(SPAWNABLE_TYPES);
    const profile = SPEED_PROFILE[actorType];
    return {
      id: `${this.actorIdPrefix}-${this.nextId++}`,
      actorType,
      route,
      sampled: samplePath(route.path, this.junction.center[1]),
      progress,
      speed: randRange(profile.cruiseMin, profile.cruiseMax),
      targetSpeed: randRange(profile.cruiseMin, profile.cruiseMax),
      state: 'CRUISE',
      nextDecisionAt: 0,
      weavePhase: Math.random() * Math.PI * 2,
      laneOffsetM: 0,
      targetLaneOffsetM: 0,
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

  private requiresStopAtLine(v: SimVehicle): boolean {
    if (v.route.control === 'NONE' || v.route.stopLineFraction === null) return false;
    if (v.progress > v.route.stopLineFraction + 0.015) return false;
    if (v.route.control === 'GATE') return !this.gateOpen();
    const phase = this.signalPhase();
    if (v.route.control === 'SIGNAL_GROUP_A') return phase !== 'A';
    if (v.route.control === 'SIGNAL_GROUP_B') return phase !== 'B';
    return false;
  }

  private stopLineSpeedLimit(v: SimVehicle, brakeDecel: number): number | null {
    if (!this.requiresStopAtLine(v) || v.route.stopLineFraction === null) return null;
    // Keep a vehicle-length buffer before the stop line. The square-root
    // limit is the maximum speed from which it can still brake to that point,
    // producing an approach queue instead of an instant stop in the road.
    const distanceToStopM = Math.max(0, (v.route.stopLineFraction - v.progress) * v.sampled.totalLength - 1.5);
    return distanceToStopM <= 0.75 ? 0 : Math.sqrt(2 * brakeDecel * distanceToStopM);
  }

  private positionFor(v: SimVehicle, progress = v.progress): Point {
    const point = v.sampled.pointAt(progress);
    const heading = v.sampled.headingAt(progress) * Math.PI / 180;
    const lonScale = 111_320 * Math.cos(point[1] * Math.PI / 180);
    return [
      point[0] + Math.cos(heading) * v.laneOffsetM / lonScale,
      point[1] - Math.sin(heading) * v.laneOffsetM / 111_320,
    ];
  }

  /** Whether an incoming vehicle can enter this junction at the given shared
   * road endpoint.  This uses geometry, not a fixed junction pairing. */
  canAcceptAt(position: Point): boolean {
    return this.junction.routes.some((route) => distanceMeters(route.path[0], position, this.junction.center[1]) < 2);
  }

  /** Admit at a shared endpoint.  Low traffic samples a legal outgoing
   * movement; a queued junction uses the least-loaded legal movement. */
  acceptTransfer(vehicle: TransferredVehicle, entryPosition: Point, congestionAware: boolean): boolean {
    const candidates = this.junction.routes.filter(
      (route) => distanceMeters(route.path[0], entryPosition, this.junction.center[1]) < 2,
    );
    if (!candidates.length) return false;
    const route = congestionAware ? this.leastLoadedRoute(candidates) : pick(candidates);
    const spawned = this.spawnVehicle(0);
    Object.assign(spawned, { id: vehicle.actorId, actorType: vehicle.actorType, route, sampled: samplePath(route.path, this.junction.center[1]), progress: 0 });
    this.vehicles.push(spawned);
    return true;
  }

  respawn(vehicle: TransferredVehicle): void {
    const spawned = this.spawnVehicle(0);
    Object.assign(spawned, { id: vehicle.actorId, actorType: vehicle.actorType });
    this.vehicles.push(spawned);
  }

  isCongested(): boolean {
    const stopped = this.vehicles.filter((vehicle) => vehicle.state === 'STOPPED').length;
    return stopped >= 2 || this.vehicles.length >= 10;
  }

  /** Car-following and overtaking policy for vehicles sharing a directed
   * route. A faster follower moves smoothly into a free adjacent lane; if it
   * cannot, it brakes behind the leader instead of occupying the same body. */
  private applyFollowingPhysics(): void {
    const routes = new Map<string, SimVehicle[]>();
    for (const vehicle of this.vehicles) {
      // A route splits at the junction, but vehicles sharing its entry arm
      // are still in the same physical lane until that split. Grouping by the
      // entry point makes following/overtaking work for cars that intend to
      // turn differently, rather than only for identical end-to-end routes.
      const entry = vehicle.route.path[0];
      const entryHeading = Math.round(vehicle.sampled.headingAt(0) / 15) * 15;
      const corridorKey = `${entry[0].toFixed(6)}:${entry[1].toFixed(6)}:${entryHeading}`;
      const group = routes.get(corridorKey) ?? [];
      group.push(vehicle);
      routes.set(corridorKey, group);
    }
    for (const group of routes.values()) {
      group.sort((a, b) => b.progress - a.progress);
      for (let index = 1; index < group.length; index++) {
        const leader = group[index - 1];
        const follower = group[index];
        const gapM = (leader.progress - follower.progress) * follower.sampled.totalLength;
        const desiredGapM = 5.5 + follower.speed * 1.15;
        const isClosing = follower.speed > leader.speed + 0.8;
        const alternateLane = follower.laneOffsetM >= 0 ? -2.2 : 2.2;
        const laneClear = !group.some((other) => other !== follower
          && Math.abs(other.progress - follower.progress) * follower.sampled.totalLength < desiredGapM * 1.3
          && Math.abs(other.laneOffsetM - alternateLane) < 1.2);
        // Do not overtake after the routes have begun to split through the
        // junction; at that point yielding/braking is the safe action.
        const beforeJunctionSplit = follower.progress < 0.42 && leader.progress < 0.52;
        if (gapM < desiredGapM && isClosing && beforeJunctionSplit && laneClear) {
          follower.targetLaneOffsetM = alternateLane;
          follower.state = 'CRUISE';
        } else if (gapM < desiredGapM && Math.abs(follower.laneOffsetM - leader.laneOffsetM) < 1.2) {
          // A stationary queue near the entry is a network-level rerouting
          // trigger. The replacement route has the same physical entry arm,
          // so this is a turn decision before the junction, not a teleport.
          if (leader.speed < 1.5 && follower.progress < 0.25 && this.rerouteFromEntry(follower)) continue;
          follower.targetLaneOffsetM = 0;
          follower.targetSpeed = Math.min(follower.targetSpeed, Math.max(0, leader.speed - (desiredGapM - gapM) * 0.7));
          follower.state = 'BRAKE';
        } else if (gapM > desiredGapM * 1.8) {
          follower.targetLaneOffsetM = 0;
        }
      }
    }
  }

  /** Yield before two different routes occupy the same physical conflict
   * area. This is deliberately independent of the risk-alert layer: alerts
   * explain a predicted conflict, while this is the simulator's vehicle-body
   * safety constraint that prevents visual phasing. */
  private applyConflictYielding(): void {
    for (let index = 0; index < this.vehicles.length; index++) {
      const first = this.vehicles[index];
      const firstPosition = first.sampled.pointAt(first.progress);
      for (let otherIndex = index + 1; otherIndex < this.vehicles.length; otherIndex++) {
        const second = this.vehicles[otherIndex];
        if (first.route.id === second.route.id) continue;
        const secondPosition = second.sampled.pointAt(second.progress);
        const currentDistance = distanceMeters(firstPosition, secondPosition, this.junction.center[1]);
        if (currentDistance > 16) continue;
        const firstFuture = first.sampled.pointAt(Math.min(1, first.progress + (first.speed * 1.8) / first.sampled.totalLength));
        const secondFuture = second.sampled.pointAt(Math.min(1, second.progress + (second.speed * 1.8) / second.sampled.totalLength));
        const predictedDistance = distanceMeters(firstFuture, secondFuture, this.junction.center[1]);
        if (currentDistance > 6 && predictedDistance > 3.5) continue;
        // The vehicle farther from its own route's conflict area yields. A
        // stable ID tie-break avoids both drivers alternately stopping.
        const firstYields = first.progress < second.progress
          || (Math.abs(first.progress - second.progress) < 0.03 && first.id > second.id);
        const yielding = firstYields ? first : second;
        const yieldingProfile = SPEED_PROFILE[yielding.actorType];
        const yieldingDistance = Math.max(0, currentDistance - 5.0);
        yielding.targetSpeed = Math.min(
          yielding.targetSpeed,
          Math.sqrt(2 * yieldingProfile.brakeDecel * yieldingDistance),
        );
        yielding.targetLaneOffsetM = 0;
        yielding.state = 'BRAKE';
      }
    }
  }

  tick(dtMs: number): { vehicles: VehicleState[]; signals: TrafficSignalState[]; exits: TransferredVehicle[] } {
    this.clockMs += dtMs;
    const dt = Math.min(0.5, dtMs / 1000);
    const now = this.clockMs;

    const exits: TransferredVehicle[] = [];
    const activeVehicles: SimVehicle[] = [];
    for (const v of this.vehicles) {
      const profile = SPEED_PROFILE[v.actorType];

      const stopLimit = this.stopLineSpeedLimit(v, profile.brakeDecel);
      if (stopLimit !== null) {
        v.targetSpeed = Math.min(v.targetSpeed, stopLimit);
        v.state = stopLimit === 0 ? 'STOPPED' : 'BRAKE';
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

    }

    this.applyFollowingPhysics();
    this.applyConflictYielding();

    for (const v of this.vehicles) {
      const profile = SPEED_PROFILE[v.actorType];
      const accel = v.speed < v.targetSpeed ? profile.accel : profile.brakeDecel;
      const maxDelta = accel * dt;
      const diff = v.targetSpeed - v.speed;
      v.speed = Math.max(0, v.speed + Math.max(-maxDelta, Math.min(maxDelta, diff)));

      const fracDelta = v.sampled.totalLength > 0 ? (v.speed * dt) / v.sampled.totalLength : 0;
      const proposedProgress = v.progress + fracDelta;
      const proposedPosition = this.positionFor(v, proposedProgress);
      const overlapsAnotherBody = this.vehicles.some((other) => other !== v
        && distanceMeters(proposedPosition, this.positionFor(other), this.junction.center[1]) < 3.1);
      if (overlapsAnotherBody) {
        // Last safety envelope: never advance a body into an occupied space.
        // Following physics will turn this into a queue on the next frame.
        v.speed = 0;
        v.targetSpeed = 0;
        v.state = 'STOPPED';
      } else {
        v.progress = proposedProgress;
      }
      v.weavePhase += dt;
      const laneDelta = v.targetLaneOffsetM - v.laneOffsetM;
      v.laneOffsetM += Math.sign(laneDelta) * Math.min(Math.abs(laneDelta), 1.8 * dt);

      if (v.progress >= 1) {
        const position = v.sampled.pointAt(1);
        exits.push({ actorId: v.id, actorType: v.actorType, position });
        continue;
      }
      activeVehicles.push(v);
    }
    this.vehicles = activeVehicles;

    const nowIso = new Date().toISOString();
    const vehicles: VehicleState[] = this.vehicles.map((v) => {
      const pos = v.sampled.pointAt(v.progress);
      const heading = v.sampled.headingAt(v.progress);
      const profile = SPEED_PROFILE[v.actorType];
      const weaveM = profile.weave ? Math.sin(v.weavePhase * 2.2) * profile.weave : 0;
      const lateralM = v.laneOffsetM + weaveM;
      const headingRadians = heading * Math.PI / 180;
      const lonScale = 111_320 * Math.cos(pos[1] * Math.PI / 180);
      return {
        schema_version: '1.0',
        actor_id: v.id,
        actor_type: v.actorType,
        ts: nowIso,
        position: {
          lat: pos[1] - Math.sin(headingRadians) * lateralM / 111_320,
          lon: pos[0] + Math.cos(headingRadians) * lateralM / lonScale,
        },
        position_uncertainty_m: 1.5,
        speed_mps: v.speed,
        acceleration_mps2: v.state === 'BURST' ? profile.accel : v.state === 'BRAKE' || v.state === 'STOPPED' ? -profile.brakeDecel : 0,
        heading_deg: heading,
        yaw_rate_dps: 0,
        road_segment_id: `${this.junctionId}:${v.route.id}`,
        lane_id: `${this.junctionId}:${v.route.id}:${Math.round(v.laneOffsetM)}`,
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
        signal_id: `${this.junctionId}-signal-a`,
        junction_id: this.junctionId,
        ts: nowIso,
        phases: [{ movement_id: 'A', state: phase === 'A' ? 'GREEN' : phase === 'NONE' ? 'AMBER' : 'RED' }],
        controller_mode: 'FIXED',
        source: 'SIMULATION',
        confidence: 1,
        position: { lat: sig.groupA[1], lon: sig.groupA[0] },
      });
      signals.push({
        signal_id: `${this.junctionId}-signal-b`,
        junction_id: this.junctionId,
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
        signal_id: `${this.junctionId}-gate`,
        junction_id: this.junctionId,
        ts: nowIso,
        phases: [{ movement_id: 'ROAD', state: open ? 'GREEN' : 'RED' }],
        controller_mode: 'FIXED',
        source: 'SIMULATION',
        confidence: 1,
        position: { lat: gate.position[1], lon: gate.position[0] },
      });
    }

    return { vehicles, signals, exits };
  }

  private leastLoadedRoute(candidates: RouteDef[]): RouteDef {
    const loads = new Map<string, number>();
    for (const vehicle of this.vehicles) loads.set(vehicle.route.id, (loads.get(vehicle.route.id) ?? 0) + 1);
    const minimum = Math.min(...candidates.map((route) => loads.get(route.id) ?? 0));
    return pick(candidates.filter((route) => (loads.get(route.id) ?? 0) === minimum));
  }

  private chooseRoute(candidates: RouteDef[]): RouteDef {
    return this.isCongested() ? this.leastLoadedRoute(candidates) : pick(candidates);
  }

  private rerouteFromEntry(vehicle: SimVehicle): boolean {
    const entry = vehicle.route.path[0];
    const candidates = this.junction.routes.filter(
      (route) => distanceMeters(route.path[0], entry, this.junction.center[1]) < 1,
    );
    const alternative = this.leastLoadedRoute(candidates);
    if (alternative.id === vehicle.route.id) return false;
    vehicle.route = alternative;
    vehicle.sampled = samplePath(alternative.path, this.junction.center[1]);
    vehicle.progress = Math.min(vehicle.progress, 0.25);
    vehicle.targetLaneOffsetM = 0;
    return true;
  }
}
