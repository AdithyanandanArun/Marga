import type { PedestrianState, TrafficSignalState, VehicleState } from '../types/canonical';
import { distanceMeters } from './geometry';
import { buildJunctionNetwork, JunctionNetworkEngine } from './networkEngine';
import type { SimulationIncident } from './vehicleEngine';

const NETWORK_LAT = 12.9550;
const NETWORK_LON = 77.6200;
const SIMULATION_TIME_SCALE = 1.8;
const SIMULATION_STEP_MS = 22;
// Publish canonical telemetry at the original four batches per second.
const TELEMETRY_INTERVAL_MS = 250;
// Applied RL actions are drained on a slower cadence than telemetry; signal
// phases change on the order of seconds, so 1 Hz is ample and keeps the
// control loop clearly separated from the movement feed.
const SIGNAL_COMMAND_INTERVAL_MS = 1000;
const PRODUCER_LEASE_KEY = 'marga-network-telemetry-producer-v1';
const PRODUCER_LEASE_MS = 3_500;
const PRODUCER_LEASE_REFRESH_MS = 1_000;
const ACTOR_TYPE_FOR_ADAPTER: Record<VehicleState['actor_type'], string> = {
  CAR: 'car', BIKE: 'motorcycle', AUTO: 'auto_rickshaw', BUS: 'bus', TRUCK: 'truck', AMBULANCE: 'emergency', OTHER: 'other',
};
const FEATURE_NETWORK = buildJunctionNetwork(NETWORK_LAT, NETWORK_LON);

type Point = [number, number];
const pedestrianCrossings: Point[][] = FEATURE_NETWORK.junctions.flatMap((junction) =>
  junction.crosswalks.filter((_, index) => index % 5 === 2).slice(0, 2));

interface PedestrianProfile {
  speedMps: number;
  direction: 1 | -1;
  pauseUntilMs: number;
  nextPauseAtMs: number;
}

const pedestrianPlans = pedestrianCrossings.flatMap((crossing, crossingIndex) => [
  { crossing, direction: 1 as const, crossingIndex },
  { crossing, direction: -1 as const, crossingIndex },
]);

const pedestrianProfiles: PedestrianProfile[] = pedestrianPlans.map((plan, index) => ({
  // Stable human walking speeds: slower walkers and brisk walkers, all within
  // a realistic range rather than a new random speed every animation frame.
  speedMps: 0.55 + Math.random() * 0.85,
  // Each crossing gets a pair moving in opposite directions. Additional
  // speed/pause variation keeps them from looking like cloned actors.
  direction: plan.direction,
  pauseUntilMs: 0,
  nextPauseAtMs: 3_000 + Math.random() * 4_000 + index * 700,
}));

export interface NetworkFrame {
  vehicles: VehicleState[];
  pedestrians: PedestrianState[];
  signals: TrafficSignalState[];
  incidents: SimulationIncident[];
  despawnedActorIds: string[];
}
export type FeedState = 'connecting' | 'live' | 'offline';

/** One in-browser adapter runtime shared by the Control Center and simulator.
 * It is intentionally a singleton: navigating between views cannot create a
 * second, divergent traffic world or freeze the gateway feed. */
class NetworkTelemetryRuntime {
  // A 30-vehicle mixed-traffic baseline makes queues and conflict prediction
  // visible without turning the map into an unreadable wall of actors.
  private engine = new JunctionNetworkEngine(buildJunctionNetwork(NETWORK_LAT, NETWORK_LON), 30, 0.65);
  private subscribers = new Set<(frame: NetworkFrame) => void>();
  private statusSubscribers = new Set<(status: FeedState) => void>();
  private frameHandle: number | null = null;
  private lastTick = 0;
  private lastPublish = 0;
  private publishing = false;
  private feedState: FeedState = 'connecting';
  private references = 0;
  private paused = false;
  private lastSignalPoll = 0;
  private pollingSignals = false;
  private topologiesRegistered = false;
  private lastSimulationTimestamp = 0;
  private simulationFrame: ReturnType<JunctionNetworkEngine['tick']> | null = null;
  private pedestrianProgress: number[] = pedestrianPlans.map((plan) => plan.direction === 1 ? 0.08 : 0.92);
  private lastPedestrianTimestamp = 0;
  private readonly producerId = crypto.randomUUID();
  private ownsProducerLease = false;
  private lastLeaseRefresh = 0;

  private pedestriansAt(timestampMs: number, vehicles: VehicleState[] = []): PedestrianState[] {
    const dtS = this.lastPedestrianTimestamp > 0
      ? Math.min(0.5, Math.max(0, timestampMs - this.lastPedestrianTimestamp) / 1000)
      : 0;
    this.lastPedestrianTimestamp = timestampMs;
    return pedestrianPlans.map((plan, index) => {
      const crossing = plan.crossing;
      const [start, end] = crossing;
      const profile = pedestrianProfiles[index];
      let progress = this.pedestrianProgress[index];
      const current: Point = [
        start[0] + (end[0] - start[0]) * progress,
        start[1] + (end[1] - start[1]) * progress,
      ];
      const crossingActive = progress > 0.04 && progress < 0.96;
      const movingVehicleNear = crossingActive && vehicles.some((vehicle) =>
        vehicle.speed_mps > 0.35
        && distanceMeters([vehicle.position.lon, vehicle.position.lat], current, current[1]) < 14,
      );
      if (timestampMs >= profile.nextPauseAtMs && timestampMs >= profile.pauseUntilMs) {
        profile.pauseUntilMs = timestampMs + 500 + ((index * 431) % 1_300);
        profile.nextPauseAtMs = profile.pauseUntilMs + 3_500 + ((index * 719) % 4_500);
      }
      const canWalk = !movingVehicleNear && timestampMs >= profile.pauseUntilMs;
      if (canWalk && dtS > 0) {
        const lengthM = distanceMeters(start, end, current[1]);
        progress += profile.direction * (profile.speedMps * dtS) / Math.max(1, lengthM);
        if (progress >= 0.98 || progress <= 0.02) {
          progress = Math.max(0.02, Math.min(0.98, progress));
          profile.direction = profile.direction === 1 ? -1 : 1;
        }
        this.pedestrianProgress[index] = progress;
      }
      const lon = start[0] + (end[0] - start[0]) * progress;
      const lat = start[1] + (end[1] - start[1]) * progress;
      const heading = (Math.atan2(end[0] - start[0], end[1] - start[1]) * 180 / Math.PI + 360) % 360;
      return {
        schema_version: '1.0', actor_id: `network-pedestrian-${index}`, ts: new Date().toISOString(),
        position: { lat, lon }, position_uncertainty_m: 1.2, speed_mps: canWalk ? profile.speedMps : 0,
        heading_deg: profile.direction === 1 ? heading : (heading + 180) % 360,
        road_segment_id: `crosswalk-${plan.crossingIndex}`, source: 'SIMULATION',
        path_hint: 'zebra-crossing', road_context: 'CROSSWALK', confidence: 0.95,
      } as PedestrianState;
    });
  }

  retain(onFrame?: (frame: NetworkFrame) => void, onStatus?: (status: FeedState) => void): () => void {
    this.references += 1;
    if (onFrame) this.subscribers.add(onFrame);
    if (onStatus) {
      this.statusSubscribers.add(onStatus);
      onStatus(this.feedState);
    }
    this.ensureRunning();
    let released = false;
    return () => {
      // A release must be idempotent. React can invoke an effect cleanup more
      // than once; a second decrement would drive the count negative and the
      // matching retain would then never bring it back above zero, leaving the
      // world permanently frozen.
      if (released) return;
      released = true;
      this.references = Math.max(0, this.references - 1);
      if (onFrame) this.subscribers.delete(onFrame);
      if (onStatus) this.statusSubscribers.delete(onStatus);
      if (this.references <= 0 && this.frameHandle !== null) {
        cancelAnimationFrame(this.frameHandle);
        this.frameHandle = null;
        this.lastTick = 0;
        this.releaseProducerLease();
      }
    };
  }

  setVehicleCount(count: number): void { this.engine.setVehicleCount(count); }
  setChaos(chaos: number): void { this.engine.setChaos(chaos); }
  setPaused(paused: boolean): void { this.paused = paused; }
  /** Pause lives on the shared runtime, not in a view. A remounted page must
   * read it back, or its button claims the world is running while it is not. */
  isPaused(): boolean { return this.paused; }

  reset(count: number): void {
    this.engine.reset(count);
    this.lastSimulationTimestamp = 0;
    this.simulationFrame = null;
    // A rebuilt network is a new set of junctions; re-announce them so the
    // controller never acts on a topology that no longer exists.
    this.topologiesRegistered = false;
  }

  private ensureRunning(): void {
    if (this.frameHandle !== null) return;
    const tick = (timestamp: number) => {
      // The next frame is scheduled in `finally`, so the world keeps moving
      // even if one tick throws. Rescheduling at the end of the body instead
      // means a single failure — a renderer disposed mid-frame, one bad
      // subscriber — permanently kills the only animation loop in the app,
      // and `frameHandle` still holds the spent id so `ensureRunning` refuses
      // to restart it. That is unrecoverable without a page reload.
      try {
        // Multiple tabs used to run independent 30-vehicle simulations and
        // publish all of them into one gateway. This small browser lease makes
        // exactly one tab the telemetry producer; other Control Center tabs
        // render the shared gateway stream without multiplying work.
        if (!this.claimProducerLease(timestamp)) {
          this.lastTick = timestamp;
          return;
        }
        const dt = this.lastTick ? Math.min(200, timestamp - this.lastTick) : 16;
        this.lastTick = timestamp;
        const pedestrianTimestamp = this.paused ? this.lastPedestrianTimestamp : timestamp;
        const pedestriansBeforeTick = this.pedestriansAt(pedestrianTimestamp);
        if (!this.simulationFrame || timestamp - this.lastSimulationTimestamp >= SIMULATION_STEP_MS) {
          const simulationDt = this.lastSimulationTimestamp > 0
            ? Math.min(200, timestamp - this.lastSimulationTimestamp)
            : dt;
          this.lastSimulationTimestamp = timestamp;
          this.simulationFrame = this.engine.tick(
            this.paused ? 0 : simulationDt * SIMULATION_TIME_SCALE,
            pedestriansBeforeTick.map((pedestrian) => [pedestrian.position.lon, pedestrian.position.lat]),
          );
        }
        const frame = { ...this.simulationFrame, pedestrians: pedestriansBeforeTick };
        // Re-evaluate only the displayed walking/paused state after vehicle
        // movement. The same timestamp gives zero pedestrian displacement, so
        // a person can pause immediately when a moving vehicle reaches them.
        frame.pedestrians = this.pedestriansAt(pedestrianTimestamp, frame.vehicles);
        this.subscribers.forEach((subscriber) => {
          // One subscriber's failure must not stop the others from rendering.
          try { subscriber(frame); } catch { /* renderer detached or failed */ }
        });
        if (timestamp - this.lastPublish >= TELEMETRY_INTERVAL_MS && !this.publishing) {
          this.lastPublish = timestamp;
          this.publishing = true;
          void publishFrame(frame.vehicles, frame.pedestrians, frame.signals, frame.incidents, frame.despawnedActorIds)
            .then(() => this.setFeedState('live'))
            .catch(() => this.setFeedState('offline'))
            .finally(() => { this.publishing = false; });
        }
        if (timestamp - this.lastSignalPoll >= SIGNAL_COMMAND_INTERVAL_MS && !this.pollingSignals) {
          this.lastSignalPoll = timestamp;
          this.pollingSignals = true;
          void this.syncSignalControl().finally(() => { this.pollingSignals = false; });
        }
      } catch {
        // Movement is the last thing that should stop; drop the frame and go on.
      } finally {
        this.frameHandle = this.references > 0 ? requestAnimationFrame(tick) : null;
      }
    };
    this.frameHandle = requestAnimationFrame(tick);
  }

  private claimProducerLease(timestamp: number): boolean {
    if (timestamp - this.lastLeaseRefresh < PRODUCER_LEASE_REFRESH_MS) return this.ownsProducerLease;
    this.lastLeaseRefresh = timestamp;
    try {
      const now = Date.now();
      const current = JSON.parse(localStorage.getItem(PRODUCER_LEASE_KEY) ?? 'null') as { id?: string; expiresAt?: number } | null;
      const isActiveTab = document.visibilityState === 'visible';
      // The visible tab is the operator's current view. Let it take ownership
      // from a hidden/background tab instead of leaving the demo apparently
      // frozen while an old tab holds the lease.
      if (current?.id && current.id !== this.producerId && (current.expiresAt ?? 0) > now && !isActiveTab) {
        this.ownsProducerLease = false;
        return false;
      }
      const lease = { id: this.producerId, expiresAt: now + PRODUCER_LEASE_MS };
      localStorage.setItem(PRODUCER_LEASE_KEY, JSON.stringify(lease));
      const confirmed = JSON.parse(localStorage.getItem(PRODUCER_LEASE_KEY) ?? 'null') as { id?: string } | null;
      this.ownsProducerLease = confirmed?.id === this.producerId;
      return this.ownsProducerLease;
    } catch {
      // Storage can be disabled in privacy modes. In that case preserve the
      // original single-tab behaviour rather than stopping all telemetry.
      this.ownsProducerLease = true;
      return true;
    }
  }

  private releaseProducerLease(): void {
    if (!this.ownsProducerLease) return;
    try {
      const current = JSON.parse(localStorage.getItem(PRODUCER_LEASE_KEY) ?? 'null') as { id?: string } | null;
      if (current?.id === this.producerId) localStorage.removeItem(PRODUCER_LEASE_KEY);
    } catch { /* storage unavailable */ }
    this.ownsProducerLease = false;
    this.lastLeaseRefresh = 0;
  }

  /** Announce junction topologies, then apply any RL actions the controller
   * has already approved. Registration is retried until it succeeds so a
   * simulator started before the gateway still becomes controllable. */
  private async syncSignalControl(): Promise<void> {
    try {
      if (!this.topologiesRegistered) {
        const topologies = this.engine.signalTopologies();
        const results = await Promise.all(topologies.map((topology) => fetch('/v1/signals/topologies', {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(topology),
        })));
        if (results.length > 0 && results.every((response) => response.ok)) {
          this.topologiesRegistered = true;
        } else {
          return;
        }
      }
      const response = await fetch('/v1/signals/commands/pending');
      if (!response.ok) return;
      const { commands } = await response.json() as { commands: Array<{ signal_id?: string; action?: string }> };
      for (const command of commands ?? []) {
        if (!command.signal_id || !command.action) continue;
        this.engine.applySignalAction(command.signal_id, command.action);
      }
    } catch {
      // The control loop is optional; movement and telemetry must not stop
      // because the gateway is briefly unavailable.
    }
  }

  private setFeedState(status: FeedState): void {
    if (this.feedState === status) return;
    this.feedState = status;
    this.statusSubscribers.forEach((subscriber) => subscriber(status));
  }
}

async function publishFrame(
  vehicles: VehicleState[],
  pedestrians: PedestrianState[],
  signals: TrafficSignalState[],
  incidents: SimulationIncident[],
  despawnedActorIds: string[],
): Promise<void> {
  const events = [
    ...vehicles.map((vehicle) => ({
    event_type: 'actor.state.updated', timestamp_utc: vehicle.ts, source: 'junction-network',
    payload: { ...vehicle, vehicle_id: vehicle.actor_id, vehicle_type: ACTOR_TYPE_FOR_ADAPTER[vehicle.actor_type] },
    })),
    ...pedestrians.map((pedestrian) => ({
      event_type: 'actor.state.updated', timestamp_utc: pedestrian.ts, source: 'junction-network',
      payload: { ...pedestrian, pedestrian_id: pedestrian.actor_id, actor_type: 'PEDESTRIAN' },
    })),
  ];
  const telemetry = await fetch('/v1/world-state/ingest', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ events }),
  });
  if (!telemetry.ok) throw new Error(`vehicle ingest failed (${telemetry.status})`);
  const signalPayload = signals.map((signal) => {
    const movements = Object.fromEntries((signal.phases ?? []).map((phase) => [phase.movement_id, phase.state]));
    const currentPhase = signal.phases?.find((phase) => phase.state === 'GREEN')?.movement_id ?? signal.phases?.[0]?.state ?? 'RED';
    return { schema_version: '1.0', signal_id: signal.signal_id, intersection_id: signal.intersection_id ?? signal.junction_id ?? 'junction-network', ts: signal.ts, position: signal.position, current_phase: currentPhase, movements, source: signal.source };
  });
  const signalResponse = await fetch('/v1/ingest/signal-states', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(signalPayload),
  });
  if (!signalResponse.ok) throw new Error(`signal ingest failed (${signalResponse.status})`);
  const incidentResponses = await Promise.all([
    ...incidents.map((incident) => fetch('/v1/ingest/hazard-observation', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        schema_version: '1.0',
        hazard_id: incident.incidentId,
        timestamp_utc: new Date().toISOString(),
        // The canonical hazard vocabulary calls this a stalled vehicle; the
        // evidence carries the stronger accident classification and trace.
        hazard_type: 'stalled_vehicle',
        road_segment_id: incident.roadSegmentId,
        position: {
          lat: incident.position[1], lon: incident.position[0], uncertainty_m: 1.5,
          confidence: incident.confidence, source: 'junction-network',
        },
        confidence: incident.confidence,
        reporting_source: 'junction-network',
        corroborating_sources: [],
        evidence: incident.evidence,
      }),
    })),
    ...despawnedActorIds.map((actorId) => fetch(`/v1/world-state/actors/${encodeURIComponent(actorId)}`, { method: 'DELETE' })),
  ]);
  if (incidentResponses.some((response) => !response.ok && response.status !== 404)) {
    throw new Error('incident publication failed');
  }
}

export const networkTelemetry = new NetworkTelemetryRuntime();
