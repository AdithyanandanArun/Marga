import type { VehicleState, TrafficSignalState, ActorType } from '../types/canonical';
import { distanceMeters, samplePath, type Point, type SampledPath } from './geometry';
import { bodiesOverlap, dimensions, type BodyPose } from './vehicleBody';
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
type StopReason = 'CONTROL' | 'QUEUE' | 'CONFLICT' | 'ACCIDENT' | null;

export interface SimulationIncident {
  incidentId: string;
  classification: 'ACCIDENT';
  actorId: string;
  affectedActorIds: string[];
  reroutedActorIds: string[];
  roadSegmentId: string;
  position: Point;
  detectedAtMs: number;
  confidence: number;
  evidence: Record<string, number | string | string[]>;
}

interface SimVehicle {
  id: string;
  actorType: ActorType;
  route: RouteDef;
  sampled: SampledPath;
  progress: number;
  speed: number;
  targetSpeed: number;
  desiredSpeed: number;
  state: SimState;
  nextDecisionAt: number;
  weavePhase: number;
  laneOffsetM: number;
  targetLaneOffsetM: number;
  stoppedSinceMs: number | null;
  stopReason: StopReason;
  incidentReported: boolean;
  retireAtMs: number | null;
}

export interface TransferredVehicle {
  actorId: string;
  actorType: ActorType;
  position: Point;
}

/** Canonical `SignalJunctionTopology` payload published to the RL controller. */
export interface SignalTopologyPayload {
  junction_id: string;
  signal_id: string;
  approaches: Array<{
    movement_id: string;
    incoming_edge_ids: string[];
    downstream_edge_ids: string[];
    approach_length_m: number;
  }>;
  phase_index_by_name: Record<string, number>;
  phase_count: number;
  default_phase_duration_s: number;
  source: string;
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
  private pendingSpawns = 0;
  private externalBodies: () => BodyPose[] = () => [];
  /** Shifts the fixed-time signal cycle so an applied RL action changes the
   * phase actually shown to traffic. Negative values hold the current phase
   * longer; positive values bring the next phase forward. */
  private signalOffsetMs = 0;

  setExternalBodies(provider: () => BodyPose[]): void { this.externalBodies = provider; }
  bodies(): BodyPose[] { return this.vehicles.map(v => this.pose(v)); }
  private pose(v: SimVehicle, progress = v.progress, lane = v.laneOffsetM): BodyPose {
    return { id: v.id, type: v.actorType, position: this.positionFor(v, progress, lane), heading: v.sampled.headingAt(progress) };
  }
  private clearBody(body: BodyPose, margin = 0.3): boolean {
    return ![...this.bodies(), ...this.externalBodies()].some(other => other.id !== body.id && bodiesOverlap(body, other, margin));
  }
  /** Ids this pose overlaps. `bodiesOverlap` inflates both bodies, so the
   * effective separation is twice `margin`. */
  private overlappingIds(body: BodyPose, margin: number): Set<string> {
    const hits = new Set<string>();
    for (const other of [...this.bodies(), ...this.externalBodies()]) {
      if (other.id !== body.id && bodiesOverlap(body, other, margin)) hits.add(other.id);
    }
    return hits;
  }
  private nextIncidentId = 0;

  private static readonly STALL_ACCIDENT_MS = 12_000;
  private static readonly ACCIDENT_CLEARANCE_MS = 6_000;
  /** Per-body inflation for the movement sweep, so the real gap enforced is
   * twice this. Car-following owns comfortable headway (3-5 m); this guard
   * only has to stop bodies visibly intersecting, and a larger value makes
   * dense-but-legal Indian traffic refuse to move. */
  private static readonly SWEEP_MARGIN_M = 0.05;
  /** Radius around the junction centre treated as "inside the intersection":
   * comfortably outside the 22 m roundabout ring and the 16 m junction box. */
  private static readonly CONFLICT_ZONE_R_M = 26;
  /** Minimum heading difference for two vehicles to count as crossing rather
   * than following. Below this, car-following owns the spacing. */
  private static readonly CROSSING_CONFLICT_MIN_DEG = 30;

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
    this.signalOffsetMs = 0;
    this.setVehicleCount(vehicleCount);
  }

  setVehicleCount(count: number): void {
    this.pendingSpawns = Math.max(0, count - this.vehicles.length);
    while (this.pendingSpawns > 0) {
      const vehicle = this.spawnVehicle(Math.random());
      if (!vehicle) break;
      this.vehicles.push(vehicle);
      this.pendingSpawns--;
    }
    if (this.vehicles.length > count) this.vehicles.length = count;
  }

  private spawnVehicle(progress: number): SimVehicle | null {
    let candidate: SimVehicle | null = null;
    // Initial random placement used to allow two bodies to spawn on top of
    // one another. Prefer a clear legal route; only fall back after bounded
    // attempts so a deliberately dense scenario can still start.
    for (let attempt = 0; attempt < 24; attempt++) {
      const route = this.chooseRoute(this.junction.routes);
      const actorType = pick(SPAWNABLE_TYPES);
      const profile = SPEED_PROFILE[actorType];
      candidate = {
        id: `${this.actorIdPrefix}-${this.nextId++}`,
        actorType,
        route,
        sampled: samplePath(route.path, this.junction.center[1]),
        progress,
        speed: randRange(profile.cruiseMin, profile.cruiseMax),
        targetSpeed: randRange(profile.cruiseMin, profile.cruiseMax),
        desiredSpeed: randRange(profile.cruiseMin, profile.cruiseMax),
        state: 'CRUISE',
        nextDecisionAt: 0,
        weavePhase: Math.random() * Math.PI * 2,
        laneOffsetM: 0,
        targetLaneOffsetM: 0,
        stoppedSinceMs: null,
        stopReason: null,
        incidentReported: false,
        retireAtMs: null,
      };
      if (this.clearBody(this.pose(candidate), 1.5)) return candidate;
    }
    return null;
  }

  private isPositionClear(position: Point, clearanceM: number, exclude?: SimVehicle): boolean {
    return !this.vehicles.some((other) => other !== exclude
      && distanceMeters(position, this.positionFor(other), this.junction.center[1]) < clearanceM);
  }

  /** Position within the signal cycle after any externally applied action. */
  private signalCycleMs(): number {
    const sig = this.junction.signal;
    if (!sig) return 0;
    const cycle = sig.greenMs * 2 + sig.allRedMs * 2;
    return ((this.clockMs + this.signalOffsetMs) % cycle + cycle) % cycle;
  }

  private signalPhase(): 'A' | 'B' | 'NONE' {
    const sig = this.junction.signal;
    if (!sig) return 'A';
    const t = this.signalCycleMs();
    if (t < sig.greenMs) return 'A';
    if (t < sig.greenMs + sig.allRedMs) return 'NONE';
    if (t < sig.greenMs * 2 + sig.allRedMs) return 'B';
    return 'NONE';
  }

  /** True when this junction is signalised and can accept RL actions. */
  hasSignal(): boolean {
    return Boolean(this.junction.signal);
  }

  /**
   * Canonical signal topology for this junction, or null when unsignalised.
   *
   * Approach edge IDs are the same `road_segment_id` values published with
   * vehicle telemetry, so the RL controller reads live graph evidence for the
   * exact edges this junction controls rather than a guessed mapping.
   */
  signalTopology(): SignalTopologyPayload | null {
    if (!this.junction.signal) return null;
    const groups: Array<{ movement: string; control: string }> = [
      { movement: 'A', control: 'SIGNAL_GROUP_A' },
      { movement: 'B', control: 'SIGNAL_GROUP_B' },
    ];
    const approaches = groups
      .map(({ movement, control }) => ({
        movement_id: movement,
        incoming_edge_ids: this.junction.routes
          .filter((route) => route.control === control)
          .map((route) => `${this.junctionId}:${route.id}`),
        downstream_edge_ids: [],
        approach_length_m: 100,
      }))
      .filter((approach) => approach.incoming_edge_ids.length > 0);
    if (approaches.length === 0) return null;

    return {
      junction_id: this.junctionId,
      signal_id: `${this.junctionId}-signal-a`,
      approaches,
      phase_index_by_name: { A: 0, B: 1 },
      phase_count: 2,
      default_phase_duration_s: this.junction.signal.greenMs / 1000,
      source: 'junction-network-simulator',
    };
  }

  /**
   * Apply one safety-approved RL action to the live cycle.
   *
   * EXTEND_GREEN_* holds the current phase for the requested extra seconds.
   * NEXT_PHASE jumps to the next cycle boundary. HOLD is intentionally a
   * no-op. Returns false when the action cannot apply, so the caller can
   * report a real failure instead of silently dropping the command.
   */
  applySignalAction(action: string): boolean {
    const sig = this.junction.signal;
    if (!sig) return false;
    if (action === 'HOLD') return true;

    if (action === 'EXTEND_GREEN_5' || action === 'EXTEND_GREEN_10') {
      const extendMs = action === 'EXTEND_GREEN_5' ? 5_000 : 10_000;
      this.signalOffsetMs -= extendMs;
      return true;
    }

    if (action === 'NEXT_PHASE') {
      const cycle = sig.greenMs * 2 + sig.allRedMs * 2;
      const boundaries = [
        sig.greenMs,
        sig.greenMs + sig.allRedMs,
        sig.greenMs * 2 + sig.allRedMs,
        cycle,
      ];
      const t = this.signalCycleMs();
      const next = boundaries.find((boundary) => boundary > t) ?? cycle;
      this.signalOffsetMs += next - t;
      return true;
    }

    return false;
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

  private positionFor(v: SimVehicle, progress = v.progress, laneOffsetM = v.laneOffsetM): Point {
    const point = v.sampled.pointAt(progress);
    const heading = v.sampled.headingAt(progress) * Math.PI / 180;
    const lonScale = 111_320 * Math.cos(point[1] * Math.PI / 180);
    return [
      point[0] + Math.cos(heading) * laneOffsetM / lonScale,
      point[1] - Math.sin(heading) * laneOffsetM / 111_320,
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
    const entrySample = samplePath(route.path, this.junction.center[1]);
    if (!this.isPositionClear(entrySample.pointAt(0), 7)) return false;
    const spawned = this.spawnVehicle(0);
    if (!spawned) return false;
    Object.assign(spawned, { id: vehicle.actorId, actorType: vehicle.actorType, route, sampled: entrySample, progress: 0 });
    if (!this.clearBody(this.pose(spawned), 0.5)) return false;
    this.vehicles.push(spawned);
    return true;
  }

  respawn(vehicle: TransferredVehicle): void {
    const spawned = this.spawnVehicle(0);
    if (!spawned) { this.pendingSpawns++; return; }
    Object.assign(spawned, { id: vehicle.actorId, actorType: vehicle.actorType });
    if (!this.clearBody(this.pose(spawned), 1.5)) { this.pendingSpawns++; return; }
    this.vehicles.push(spawned);
  }

  private rerouteFollowers(blocked: SimVehicle): string[] {
    const entry = blocked.route.path[0];
    const rerouted: string[] = [];
    for (const vehicle of this.vehicles) {
      if (vehicle === blocked || vehicle.progress >= blocked.progress || vehicle.progress > 0.35) continue;
      if (distanceMeters(vehicle.route.path[0], entry, this.junction.center[1]) >= 1) continue;
      const previousRoute = vehicle.route.id;
      if (this.rerouteFromEntry(vehicle) && vehicle.route.id !== previousRoute) rerouted.push(vehicle.id);
    }
    return rerouted;
  }

  /** A red signal or closed gate is a legitimate queue, never an accident. */
  private expectedControlHold(vehicle: SimVehicle): boolean {
    return this.requiresStopAtLine(vehicle);
  }

  private identifyStalledIncidents(): SimulationIncident[] {
    const incidents: SimulationIncident[] = [];
    for (const vehicle of this.vehicles) {
      if (vehicle.incidentReported || vehicle.retireAtMs !== null) continue;
      if (this.expectedControlHold(vehicle) || vehicle.speed > 0.15) {
        vehicle.stoppedSinceMs = null;
        if (vehicle.stopReason !== 'ACCIDENT') vehicle.stopReason = null;
        continue;
      }
      // Do not promote a normal yield into an accident. A reported incident
      // needs both a physical conflict hold and a road user trapped behind it.
      const entry = vehicle.route.path[0];
      const blocksFollower = this.vehicles.some((other) => other !== vehicle
        && other.progress < vehicle.progress
        && distanceMeters(other.route.path[0], entry, this.junction.center[1]) < 1
        && (vehicle.progress - other.progress) * other.sampled.totalLength < 35);
      if (vehicle.stopReason !== 'CONFLICT' || !blocksFollower) {
        vehicle.stoppedSinceMs = null;
        continue;
      }
      if (vehicle.stoppedSinceMs === null) vehicle.stoppedSinceMs = this.clockMs;
      const stationaryForMs = this.clockMs - vehicle.stoppedSinceMs;
      if (stationaryForMs < JunctionSimEngine.STALL_ACCIDENT_MS) continue;

      vehicle.incidentReported = true;
      vehicle.retireAtMs = this.clockMs + JunctionSimEngine.ACCIDENT_CLEARANCE_MS;
      vehicle.stopReason = 'ACCIDENT';
      vehicle.state = 'STOPPED';
      vehicle.speed = 0;
      vehicle.targetSpeed = 0;
      const reroutedActorIds = this.rerouteFollowers(vehicle);
      const affectedActorIds = [vehicle.id, ...reroutedActorIds];
      incidents.push({
        incidentId: `${this.junctionId}-accident-${this.nextIncidentId++}`,
        classification: 'ACCIDENT',
        actorId: vehicle.id,
        affectedActorIds,
        reroutedActorIds,
        roadSegmentId: `${this.junctionId}:${vehicle.route.id}`,
        position: this.positionFor(vehicle),
        detectedAtMs: this.clockMs,
        confidence: 0.9,
        evidence: {
          classification: 'sustained_uncontrolled_lane_blockage',
          stationary_for_s: Math.round(stationaryForMs / 100) / 10,
          affected_actor_ids: affectedActorIds,
        },
      });
    }
    return incidents;
  }

  isCongested(): boolean {
    const stopped = this.vehicles.filter((vehicle) => vehicle.state === 'STOPPED').length;
    return stopped >= 2 || this.vehicles.length >= 10;
  }

  /** A lane change is allowed only if the whole vehicle body can enter it
   * without cutting off either a faster follower or oncoming traffic. */
  private canUseAdjacentLane(vehicle: SimVehicle, laneOffsetM: number, clearanceM: number): boolean {
    const candidate = this.positionFor(vehicle, vehicle.progress, laneOffsetM);
    return !this.vehicles.some((other) => {
      if (other === vehicle) return false;
      const separation = distanceMeters(candidate, this.positionFor(other), this.junction.center[1]);
      if (separation >= clearanceM) return false;
      const headingDelta = Math.abs((((vehicle.sampled.headingAt(vehicle.progress) - other.sampled.headingAt(other.progress)) + 540) % 360) - 180);
      // Opposing road users need significantly more headway than a vehicle
      // in the same direction because their closing speed is much higher.
      return headingDelta > 100 || separation < clearanceM;
    });
  }

  /** Car-following and overtaking policy for vehicles sharing a directed
   * route. A faster follower moves smoothly into a free adjacent lane; if it
   * cannot, it brakes behind the leader instead of occupying the same body. */
  private applyFollowingPhysics(): void {
    for (const v of this.vehicles) {
      const own = this.pose(v), rad = own.heading * Math.PI / 180;
      const forward = [Math.sin(rad), Math.cos(rad)];
      const lateral = [Math.cos(rad), -Math.sin(rad)];
      const size = dimensions(v.actorType);
      for (const other of [...this.bodies(), ...this.externalBodies()]) {
        if (other.id === v.id) continue;
        const dx = (other.position[0] - own.position[0]) * 111320 * Math.cos(own.position[1] * Math.PI / 180);
        const dy = (other.position[1] - own.position[1]) * 111320;
        const along = dx * forward[0] + dy * forward[1];
        const across = Math.abs(dx * lateral[0] + dy * lateral[1]);
        const otherSize = dimensions(other.type);
        const headingDelta = Math.abs(((own.heading - other.heading + 540) % 360) - 180);
        if (along <= 0 || along > 60 || headingDelta > 45 ||
            across > (size.width + otherSize.width) / 2 + 0.4) continue;
        const leader = this.vehicles.find(x => x.id === other.id);
        const gap = along - (size.length + otherSize.length) / 2;
        const standstillGap = this.requiresStopAtLine(v) ? 5 : 3;
        const headway = standstillGap + v.speed * 1.1;
        // Maintain bumper clearance even when different routes share a lane.
        const safe = Math.max(0, (leader?.speed ?? 0) + (gap - headway) * 0.65);
        v.targetSpeed = Math.min(v.targetSpeed, safe,
          Math.sqrt(2 * SPEED_PROFILE[v.actorType].brakeDecel * Math.max(0, gap - standstillGap)));
      }
      // Returning to lane must be collision checked just like overtaking.
      if (v.laneOffsetM !== 0 && this.canUseAdjacentLane(v, 0, 12)) v.targetLaneOffsetM = 0;
    }
  }

  /** Predict pairwise proximity before vehicles enter a shared junction.
   * The earlier implementation waited until bodies were already inside the
   * conflict area. This uses a short rolling horizon so a driver eases off
   * before the point of conflict, which is how assertive traffic still avoids
   * a crash. */
  private applyPredictiveCollisionAvoidance(): void {
    const horizonS = 4.5;
    const stepS = 0.25;
    for (let i = 0; i < this.vehicles.length; i++) {
      const first = this.vehicles[i];
      for (let j = i + 1; j < this.vehicles.length; j++) {
        const second = this.vehicles[j];
        if (first.route.id === second.route.id) continue;
        // Vehicles travelling the same way are a following pair, not a
        // crossing conflict, and car-following already owns their spacing.
        // Each roundabout arm pairing is its own route id, so without this the
        // 22 m ring reads every follower as a conflict and makes the leader
        // brake for the vehicle behind it, throttling the whole circulation.
        const headingGap = Math.abs(((first.sampled.headingAt(first.progress)
          - second.sampled.headingAt(second.progress) + 540) % 360) - 180);
        if (headingGap < JunctionSimEngine.CROSSING_CONFLICT_MIN_DEG) continue;
        let conflictAt: number | null = null;
        for (let t = 0; t <= horizonS; t += stepS) {
          const firstProgress = Math.min(1, first.progress + (first.speed * t) / first.sampled.totalLength);
          const secondProgress = Math.min(1, second.progress + (second.speed * t) / second.sampled.totalLength);
          if (distanceMeters(this.positionFor(first, firstProgress), this.positionFor(second, secondProgress), this.junction.center[1]) < 6.5) {
            conflictAt = t;
            break;
          }
        }
        if (conflictAt === null) continue;
        // Priority belongs to whoever reaches the conflict point first, with
        // actor id only as a stable tie-break. Deciding purely by id starves
        // the highest id: on a shared roundabout ring it yields to every peer
        // and never moves again.
        //
        // The permitted distance must come from the geometry, not from the
        // yielder's own speed. `speed * conflictAt - 5.5` latches at zero —
        // a stopped vehicle is allowed to travel 0 m, so its target speed
        // stays 0 and it can never restart. A vehicle already inside the
        // conflict zone has distance 0, which now makes it the priority
        // vehicle so it clears the zone instead of being held inside it.
        const firstDistance = first.speed * conflictAt;
        const secondDistance = second.speed * conflictAt;
        // Traffic already inside the junction outranks traffic still
        // approaching it, so a vehicle can always clear the box it occupies.
        // This is the roundabout give-way rule — circulating before entering —
        // and it is also what stops a cross or T junction from locking up with
        // vehicles stranded across it.
        const centerLat = this.junction.center[1];
        const firstInside = distanceMeters(this.positionFor(first), this.junction.center, centerLat)
          < JunctionSimEngine.CONFLICT_ZONE_R_M;
        const secondInside = distanceMeters(this.positionFor(second), this.junction.center, centerLat)
          < JunctionSimEngine.CONFLICT_ZONE_R_M;
        const firstYields = firstInside !== secondInside
          ? !firstInside
          : firstDistance !== secondDistance
            ? firstDistance > secondDistance
            : first.id > second.id;
        const yielding = firstYields ? first : second;
        // Permit approach up to the conflict radius using the *actual* gap to
        // the other vehicle. Deriving this from the yielder's own speed makes
        // a stopped vehicle permanently stationary: at speed 0 it is allowed
        // to travel 0 m, so it never restarts even after the other has gone.
        // A real gap always lets it creep, which is how two drivers edging
        // into the same roundabout resolve who goes first.
        const separationM = distanceMeters(this.positionFor(first), this.positionFor(second), centerLat);
        const distanceBeforeConflict = Math.max(0, separationM - 6.5);
        const safeArrivalS = conflictAt + 1.5;
        yielding.targetSpeed = Math.min(yielding.targetSpeed, distanceBeforeConflict / Math.max(0.5, safeArrivalS));
        yielding.targetLaneOffsetM = 0;
        yielding.state = 'BRAKE';
        yielding.stopReason = 'CONFLICT';
      }
    }
  }

  /** Yield before two different routes occupy the same physical conflict
   * area. This is deliberately independent of the risk-alert layer: alerts
   * explain a predicted conflict, while this is the simulator's vehicle-body
   * safety constraint that prevents visual phasing. */

  tick(dtMs: number): { vehicles: VehicleState[]; signals: TrafficSignalState[]; exits: TransferredVehicle[]; incidents: SimulationIncident[]; despawnedActorIds: string[] } {
    this.clockMs += dtMs;
    const dt = Math.min(0.5, dtMs / 1000);
    const now = this.clockMs;

    const exits: TransferredVehicle[] = [];
    const activeVehicles: SimVehicle[] = [];
    const despawnedActorIds: string[] = [];
    for (const v of this.vehicles) {
      const profile = SPEED_PROFILE[v.actorType];

      v.targetSpeed = v.desiredSpeed;
      const stopLimit = this.stopLineSpeedLimit(v, profile.brakeDecel);
      if (stopLimit !== null) {
        v.targetSpeed = Math.min(v.targetSpeed, stopLimit);
        v.state = stopLimit === 0 ? 'STOPPED' : 'BRAKE';
        if (stopLimit === 0) v.stopReason = 'CONTROL';
      } else if (now >= v.nextDecisionAt) {
        const roll = Math.random();
        const burstChance = 0.1 + this.chaos * 0.3;
        const brakeChance = 0.08 + this.chaos * 0.24;
        if (roll < burstChance) {
          v.state = 'BURST';
          v.targetSpeed = randRange(profile.cruiseMax * 0.85, profile.burstMax);
          v.nextDecisionAt = now + randRange(700, 2000);
        } else if (roll < burstChance + brakeChance) {
          // Driver variability can produce firm braking, but cannot invent a
          // stationary vehicle in the middle of an open road.
          v.state = 'BRAKE';
          v.targetSpeed = randRange(Math.max(0.8, profile.cruiseMin * 0.25), profile.cruiseMin * 0.7);
          v.nextDecisionAt = now + randRange(500, 1600);
        } else {
          v.state = 'CRUISE';
          v.targetSpeed = randRange(profile.cruiseMin, profile.cruiseMax);
          v.nextDecisionAt = now + randRange(1400, 3800);
        }
      }
      if (stopLimit === null && now >= v.nextDecisionAt - 1) v.desiredSpeed = v.targetSpeed;
      if (v.retireAtMs !== null) v.targetSpeed = 0;
    }

    this.applyFollowingPhysics();
    this.applyPredictiveCollisionAvoidance();


    for (const v of this.vehicles) {
      const profile = SPEED_PROFILE[v.actorType];
      const accel = v.speed < v.targetSpeed ? profile.accel : profile.brakeDecel;
      const maxDelta = accel * dt;
      const diff = v.targetSpeed - v.speed;
      v.speed = Math.max(0, v.speed + Math.max(-maxDelta, Math.min(maxDelta, diff)));

      const fracDelta = v.sampled.totalLength > 0 ? (v.speed * dt) / v.sampled.totalLength : 0;
      const proposedProgress = v.progress + fracDelta;
      const laneDelta = v.targetLaneOffsetM - v.laneOffsetM;
      const proposedLane = v.laneOffsetM + Math.sign(laneDelta) * Math.min(Math.abs(laneDelta), 0.8 * dt);
      // Sweep both translation and heading; a long bus turning can otherwise
      // clip a stopped scooter even when its end position appears clear.
      //
      // Only *newly* contacted bodies block movement. A vehicle that already
      // overlaps someone — after a spawn, a lane merge or a junction transfer
      // — must be able to drive out of that overlap. Treating the overlap it
      // is already in as a blocker is self-sustaining: the move that would
      // resolve it is exactly the move being refused, so the vehicle stops
      // forever and everything behind it queues into a permanent jam.
      const alreadyTouching = this.overlappingIds(this.pose(v), JunctionSimEngine.SWEEP_MARGIN_M);
      let safe = true;
      const steps = Math.max(4, Math.ceil(v.speed * dt / 0.4));
      for (let step = 1; step <= steps; step++) {
        const fraction = step / steps;
        const swept = this.pose(v, v.progress + fracDelta * fraction,
          v.laneOffsetM + (proposedLane - v.laneOffsetM) * fraction);
        const hits = this.overlappingIds(swept, JunctionSimEngine.SWEEP_MARGIN_M);
        if ([...hits].some((id) => !alreadyTouching.has(id))) { safe = false; break; }
      }
      if (safe) {
        v.progress = proposedProgress;
        v.laneOffsetM = proposedLane;
      } else {
        v.speed = 0;
        v.targetSpeed = 0;
        v.state = 'STOPPED';
        v.stopReason = this.expectedControlHold(v) ? 'QUEUE' : 'CONFLICT';
      }

      if (v.retireAtMs !== null && this.clockMs >= v.retireAtMs) {
        despawnedActorIds.push(v.id);
        continue;
      }
      if (v.progress >= 1) {
        const position = v.sampled.pointAt(1);
        exits.push({ actorId: v.id, actorType: v.actorType, position });
        continue;
      }
      activeVehicles.push(v);
    }
    this.vehicles = activeVehicles;
    const incidents = this.identifyStalledIncidents();
    // Exiting actors are handed to a neighbouring junction by
    // JunctionNetworkEngine, so only retirements are replaced locally.  If
    // we refilled every exit here and also handed it off, the district would
    // slowly manufacture vehicles until every road gridlocked.
    this.pendingSpawns += despawnedActorIds.length;
    while (this.pendingSpawns > 0) {
      const spawned = this.spawnVehicle(0);
      if (!spawned) break;
      this.vehicles.push(spawned);
      this.pendingSpawns--;
    }

    const nowIso = new Date().toISOString();
    const vehicles: VehicleState[] = this.vehicles.map((v) => {
      const pos = v.sampled.pointAt(v.progress);
      const heading = v.sampled.headingAt(v.progress);
      const profile = SPEED_PROFILE[v.actorType];
      const weaveM = 0;
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

    return { vehicles, signals, exits, incidents, despawnedActorIds };
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
    const sampled = samplePath(alternative.path, this.junction.center[1]);
    const progress = vehicle.progress * vehicle.sampled.totalLength / sampled.totalLength;
    if (distanceMeters(sampled.pointAt(progress), vehicle.sampled.pointAt(vehicle.progress), this.junction.center[1]) > 0.05) return false;
    if (Math.abs(sampled.headingAt(progress) - vehicle.sampled.headingAt(vehicle.progress)) > 1) return false;
    vehicle.route = alternative;
    vehicle.sampled = sampled;
    vehicle.progress = progress;
    vehicle.targetLaneOffsetM = 0;
    return true;
  }
}
