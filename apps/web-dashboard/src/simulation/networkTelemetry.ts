import type { TrafficSignalState, VehicleState } from '../types/canonical';
import { buildJunctionNetwork, JunctionNetworkEngine } from './networkEngine';
import type { SimulationIncident } from './vehicleEngine';

const NETWORK_LAT = 12.9550;
const NETWORK_LON = 77.6200;
const SIMULATION_TIME_SCALE = 1.8;
// 4 Hz is sufficient for the displayed road motion and keeps one browser
// adapter from flooding the gateway with duplicate animation-frame updates.
const TELEMETRY_INTERVAL_MS = 250;
// Applied RL actions are drained on a slower cadence than telemetry; signal
// phases change on the order of seconds, so 1 Hz is ample and keeps the
// control loop clearly separated from the movement feed.
const SIGNAL_COMMAND_INTERVAL_MS = 1000;
const ACTOR_TYPE_FOR_ADAPTER: Record<VehicleState['actor_type'], string> = {
  CAR: 'car', BIKE: 'motorcycle', AUTO: 'auto_rickshaw', BUS: 'bus', TRUCK: 'truck', AMBULANCE: 'emergency', OTHER: 'other',
};

export interface NetworkFrame {
  vehicles: VehicleState[];
  signals: TrafficSignalState[];
  incidents: SimulationIncident[];
  despawnedActorIds: string[];
}
export type FeedState = 'connecting' | 'live' | 'offline';

/** One in-browser adapter runtime shared by the Control Center and simulator.
 * It is intentionally a singleton: navigating between views cannot create a
 * second, divergent traffic world or freeze the gateway feed. */
class NetworkTelemetryRuntime {
  private engine = new JunctionNetworkEngine(buildJunctionNetwork(NETWORK_LAT, NETWORK_LON), 24, 0.5);
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

  retain(onFrame?: (frame: NetworkFrame) => void, onStatus?: (status: FeedState) => void): () => void {
    this.references += 1;
    if (onFrame) this.subscribers.add(onFrame);
    if (onStatus) {
      this.statusSubscribers.add(onStatus);
      onStatus(this.feedState);
    }
    this.ensureRunning();
    return () => {
      this.references -= 1;
      if (onFrame) this.subscribers.delete(onFrame);
      if (onStatus) this.statusSubscribers.delete(onStatus);
      if (this.references <= 0 && this.frameHandle !== null) {
        cancelAnimationFrame(this.frameHandle);
        this.frameHandle = null;
        this.lastTick = 0;
      }
    };
  }

  setVehicleCount(count: number): void { this.engine.setVehicleCount(count); }
  setChaos(chaos: number): void { this.engine.setChaos(chaos); }
  setPaused(paused: boolean): void { this.paused = paused; }

  reset(count: number): void {
    this.engine.reset(count);
    // A rebuilt network is a new set of junctions; re-announce them so the
    // controller never acts on a topology that no longer exists.
    this.topologiesRegistered = false;
  }

  private ensureRunning(): void {
    if (this.frameHandle !== null) return;
    const tick = (timestamp: number) => {
      const dt = this.lastTick ? Math.min(200, timestamp - this.lastTick) : 16;
      this.lastTick = timestamp;
      const frame = this.engine.tick(this.paused ? 0 : dt * SIMULATION_TIME_SCALE);
      this.subscribers.forEach((subscriber) => subscriber(frame));
      if (timestamp - this.lastPublish >= TELEMETRY_INTERVAL_MS && !this.publishing) {
        this.lastPublish = timestamp;
        this.publishing = true;
        void publishFrame(frame.vehicles, frame.signals, frame.incidents, frame.despawnedActorIds)
          .then(() => this.setFeedState('live'))
          .catch(() => this.setFeedState('offline'))
          .finally(() => { this.publishing = false; });
      }
      if (timestamp - this.lastSignalPoll >= SIGNAL_COMMAND_INTERVAL_MS && !this.pollingSignals) {
        this.lastSignalPoll = timestamp;
        this.pollingSignals = true;
        void this.syncSignalControl().finally(() => { this.pollingSignals = false; });
      }
      this.frameHandle = requestAnimationFrame(tick);
    };
    this.frameHandle = requestAnimationFrame(tick);
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
  signals: TrafficSignalState[],
  incidents: SimulationIncident[],
  despawnedActorIds: string[],
): Promise<void> {
  const events = vehicles.map((vehicle) => ({
    event_type: 'actor.state.updated', timestamp_utc: vehicle.ts, source: 'junction-network',
    payload: { ...vehicle, vehicle_id: vehicle.actor_id, vehicle_type: ACTOR_TYPE_FOR_ADAPTER[vehicle.actor_type] },
  }));
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
