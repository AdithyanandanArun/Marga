import type {
  VehicleState,
  PedestrianState,
  Hazard,
  TrafficSignalState,
  RoadEvent,
  DynamicActorObservation,
  RSUState,
  RiskEvent,
  Alert,
  SystemMetrics,
  ActorType,
} from '../types/canonical';
import type { WorldDelta, WorldEntity } from '../types/events';
import { useWorldStore } from '../state/worldStore';
import { useAlertStore } from '../state/alertStore';

const CENTER = { lat: 12.9716, lon: 77.5946 };
const ACTOR_TYPES: ActorType[] = ['CAR', 'BIKE', 'AUTO', 'BUS', 'TRUCK'];

function randomOffset(range: number): number {
  return (Math.random() - 0.5) * range;
}

function uuid(): string {
  return crypto.randomUUID();
}

function generateVehicle(i: number): VehicleState {
  const heading = Math.random() * 360;
  return {
    schema_version: '1.0',
    actor_id: `veh-${i.toString().padStart(3, '0')}`,
    actor_type: ACTOR_TYPES[i % ACTOR_TYPES.length],
    ts: new Date().toISOString(),
    position: {
      lat: CENTER.lat + randomOffset(0.03),
      lon: CENTER.lon + randomOffset(0.03),
    },
    position_uncertainty_m: 2 + Math.random() * 8,
    speed_mps: 5 + Math.random() * 20,
    acceleration_mps2: (Math.random() - 0.5) * 2,
    heading_deg: heading,
    yaw_rate_dps: (Math.random() - 0.5) * 5,
    source: 'SIMULATION',
    capabilities: ['V2X_BASIC'],
    lifecycle: 'ACTIVE',
  };
}

function generatePedestrian(i: number): PedestrianState {
  return {
    schema_version: '1.0',
    actor_id: `ped-${i.toString().padStart(3, '0')}`,
    ts: new Date().toISOString(),
    position: {
      lat: CENTER.lat + randomOffset(0.02),
      lon: CENTER.lon + randomOffset(0.02),
    },
    position_uncertainty_m: 3 + Math.random() * 5,
    speed_mps: 0.5 + Math.random() * 2,
    heading_deg: Math.random() * 360,
    road_context: (['SIDEWALK', 'CROSSWALK', 'ROADWAY', 'UNKNOWN'] as const)[Math.floor(Math.random() * 4)],
    source: 'SIMULATION',
    confidence: 0.6 + Math.random() * 0.4,
  };
}

function generateHazard(i: number): Hazard {
  const types = ['POTHOLE', 'DEBRIS', 'FLOOD', 'CONSTRUCTION', 'ANIMAL'] as const;
  const pos = { lat: CENTER.lat + randomOffset(0.025), lon: CENTER.lon + randomOffset(0.025) };
  return {
    hazard_id: `haz-${i.toString().padStart(3, '0')}`,
    type: types[i % types.length],
    geometry: { type: 'Point', coordinates: [pos.lon, pos.lat] },
    severity: 0.3 + Math.random() * 0.7,
    confidence: 0.4 + Math.random() * 0.6,
    first_seen: new Date(Date.now() - 300000).toISOString(),
    last_seen: new Date().toISOString(),
    ttl_s: 600 + Math.floor(Math.random() * 3600),
    source_ids: [`src-${i}`],
    evidence_count: 1 + Math.floor(Math.random() * 5),
    state: Math.random() > 0.3 ? 'VERIFIED' : 'CANDIDATE',
  };
}

function generateSignal(i: number): TrafficSignalState {
  const states = ['RED', 'AMBER', 'GREEN'] as const;
  return {
    signal_id: `sig-${i.toString().padStart(3, '0')}`,
    junction_id: `jnc-${i}`,
    ts: new Date().toISOString(),
    phases: [
      { movement_id: 'NS', state: states[Math.floor(Math.random() * 3)] },
      { movement_id: 'EW', state: states[Math.floor(Math.random() * 3)] },
    ],
    controller_mode: 'FIXED',
    source: 'SIMULATION',
    confidence: 0.95,
  };
}

function generateRoadEvent(i: number): RoadEvent {
  const types = ['LANE_NARROWING', 'LANE_CLOSURE', 'CONSTRUCTION'] as const;
  const pos = { lat: CENTER.lat + randomOffset(0.02), lon: CENTER.lon + randomOffset(0.02) };
  return {
    event_id: `evt-${i.toString().padStart(3, '0')}`,
    type: types[i % types.length],
    geometry: {
      type: 'LineString',
      coordinates: [
        [pos.lon, pos.lat],
        [pos.lon + randomOffset(0.005), pos.lat + randomOffset(0.005)],
      ],
    },
    affected_segment_ids: [`seg-${i}`],
    affected_lane_ids: [`lane-${i}-0`],
    effective_from: new Date().toISOString(),
    severity: 0.5 + Math.random() * 0.5,
    confidence: 0.8 + Math.random() * 0.2,
    source: 'SIMULATION',
  };
}

function generateDynamicActor(i: number): DynamicActorObservation {
  const subtypes = ['COW', 'DOG', 'GOAT'] as const;
  return {
    observation_id: `dyn-${i.toString().padStart(3, '0')}`,
    actor_class: 'ANIMAL',
    subtype: subtypes[i % subtypes.length],
    ts: new Date().toISOString(),
    position: {
      lat: CENTER.lat + randomOffset(0.02),
      lon: CENTER.lon + randomOffset(0.02),
    },
    position_uncertainty_m: 5 + Math.random() * 10,
    heading_deg: Math.random() * 360,
    detector_confidence: 0.5 + Math.random() * 0.5,
    source_id: `cam-${i}`,
    source_type: 'SIMULATION',
    behavior: (['NEAR_ROAD', 'APPROACHING', 'CROSSING', 'IN_LANE'] as const)[Math.floor(Math.random() * 4)],
  };
}

function generateRSU(i: number): RSUState {
  return {
    rsu_id: `rsu-${i.toString().padStart(3, '0')}`,
    position: {
      lat: CENTER.lat + randomOffset(0.02),
      lon: CENTER.lon + randomOffset(0.02),
    },
    coverage_m: 200 + Math.random() * 300,
    capabilities: ['RELAY', 'OBSERVATION'],
    link_state: 'FULL',
    trust_identity: `trust-rsu-${i}`,
    last_heartbeat: new Date().toISOString(),
  };
}

function generateRisk(i: number, vehicles: VehicleState[]): RiskEvent | null {
  if (vehicles.length < 2) return null;
  const a = vehicles[Math.floor(Math.random() * vehicles.length)];
  const b = vehicles[Math.floor(Math.random() * vehicles.length)];
  if (a.actor_id === b.actor_id) return null;
  return {
    risk_id: `risk-${i.toString().padStart(3, '0')}`,
    type: (['REAR_END', 'INTERSECTION_CONFLICT', 'HEAD_ON', 'SIDE_SWIPE'] as const)[Math.floor(Math.random() * 4)],
    ts: new Date().toISOString(),
    affected_actor_ids: [a.actor_id, b.actor_id],
    time_to_conflict_s: 1 + Math.random() * 8,
    min_predicted_distance_m: Math.random() * 15,
    severity: 0.4 + Math.random() * 0.6,
    confidence: 0.5 + Math.random() * 0.5,
    risk_score: 0.3 + Math.random() * 0.7,
    evidence: [
      { entity_id: a.actor_id, entity_type: 'vehicle', metric: 'closing_speed_mps', value: 5 + Math.random() * 15, unit: 'm/s' },
      { entity_id: b.actor_id, entity_type: 'vehicle', metric: 'ttc_s', value: 2 + Math.random() * 5, unit: 's' },
    ],
    expires_at: new Date(Date.now() + 10000).toISOString(),
  };
}

function generateAlert(risk: RiskEvent): Alert {
  const sevMap = ['CRITICAL', 'HIGH', 'MEDIUM', 'LOW'] as const;
  const sev = risk.severity > 0.8 ? 0 : risk.severity > 0.6 ? 1 : risk.severity > 0.4 ? 2 : 3;
  return {
    alert_id: `alert-${uuid().slice(0, 8)}`,
    risk_id: risk.risk_id,
    ts: new Date().toISOString(),
    severity: sevMap[sev],
    title: `${risk.type.replace(/_/g, ' ')} detected`,
    description: `Potential ${risk.type.toLowerCase().replace(/_/g, ' ')} between ${risk.affected_actor_ids.join(' and ')}. TTC: ${risk.time_to_conflict_s.toFixed(1)}s`,
    affected_actor_ids: risk.affected_actor_ids,
    confidence: risk.confidence,
    evidence: risk.evidence,
    state: 'ACTIVE',
    issued_at: new Date().toISOString(),
    policy_version: '1.0',
  };
}

function generateMetrics(): SystemMetrics {
  return {
    actor_updates_per_sec: 200 + Math.floor(Math.random() * 300),
    event_bus_lag_ms: Math.floor(Math.random() * 20),
    risk_evaluations_per_sec: 50 + Math.floor(Math.random() * 100),
    risk_p50_ms: 5 + Math.floor(Math.random() * 10),
    risk_p95_ms: 15 + Math.floor(Math.random() * 30),
    risk_p99_ms: 30 + Math.floor(Math.random() * 50),
    alerts_issued: Math.floor(Math.random() * 20),
    alerts_cleared: Math.floor(Math.random() * 15),
    position_uncertainty_avg: 3 + Math.random() * 5,
    dropped_messages: Math.floor(Math.random() * 5),
    ws_clients: 1 + Math.floor(Math.random() * 3),
    ws_bytes_per_sec: 5000 + Math.floor(Math.random() * 15000),
    trust_rejections: Math.floor(Math.random() * 3),
    connectivity_state: 'FULL',
    simulation_speed: 1.0,
  };
}

export class FixturePlayer {
  private vehicles: VehicleState[] = [];
  private interval: ReturnType<typeof setInterval> | null = null;
  private tickCount = 0;

  start(vehicleCount = 20, tickMs = 100): void {
    this.vehicles = Array.from({ length: vehicleCount }, (_, i) => generateVehicle(i));
    const pedestrians = Array.from({ length: 5 }, (_, i) => generatePedestrian(i));
    const hazards = Array.from({ length: 3 }, (_, i) => generateHazard(i));
    const signals = Array.from({ length: 4 }, (_, i) => generateSignal(i));
    const roadEvents = Array.from({ length: 2 }, (_, i) => generateRoadEvent(i));
    const dynamicActors = Array.from({ length: 2 }, (_, i) => generateDynamicActor(i));
    const rsus = Array.from({ length: 3 }, (_, i) => generateRSU(i));

    const initialEntities: WorldEntity[] = [
      ...this.vehicles.map((v) => ({ entity_type: 'vehicle' as const, entity_id: v.actor_id, data: v })),
      ...pedestrians.map((p) => ({ entity_type: 'pedestrian' as const, entity_id: p.actor_id, data: p })),
      ...hazards.map((h) => ({ entity_type: 'hazard' as const, entity_id: h.hazard_id, data: h })),
      ...signals.map((s) => ({ entity_type: 'signal' as const, entity_id: s.signal_id, data: s })),
      ...roadEvents.map((r) => ({ entity_type: 'road_event' as const, entity_id: r.event_id, data: r })),
      ...dynamicActors.map((d) => ({ entity_type: 'dynamic_actor' as const, entity_id: d.observation_id, data: d })),
      ...rsus.map((r) => ({ entity_type: 'rsu' as const, entity_id: r.rsu_id, data: r })),
    ];

    const snapshot: WorldDelta = {
      kind: 'snapshot',
      server_time: new Date().toISOString(),
      upserts: initialEntities,
      deletes: [],
    };
    useWorldStore.getState().applyDelta(snapshot);
    useWorldStore.getState().updateMetrics(generateMetrics());

    this.interval = setInterval(() => {
      this.tick();
    }, tickMs);
  }

  private tick(): void {
    this.tickCount++;
    const now = new Date().toISOString();

    // India urban speed caps (m/s): auto ~8, bike ~6, car ~14, bus ~11, truck ~10
    const speedCap: Record<string, number> = { CAR: 14, BUS: 11, TRUCK: 10, AUTO: 8, BIKE: 6 };

    for (const v of this.vehicles) {
      const headingRad = (v.heading_deg * Math.PI) / 180;
      const dt = 0.1;  // match SUMO 100 ms step — was 0.5 s which caused teleporting
      const cap = speedCap[v.actor_type] ?? 12;
      v.speed_mps = Math.max(0, Math.min(cap, v.speed_mps + (Math.random() - 0.5) * 0.5));
      const dlat = (v.speed_mps * dt * Math.cos(headingRad)) / 111320;
      const dlon = (v.speed_mps * dt * Math.sin(headingRad)) / (111320 * Math.cos((v.position.lat * Math.PI) / 180));
      v.position.lat += dlat;
      v.position.lon += dlon;
      v.heading_deg = (v.heading_deg + (Math.random() - 0.5) * 2 + 360) % 360;
      v.position_uncertainty_m = Math.max(1, v.position_uncertainty_m + (Math.random() - 0.5) * 0.2);
      v.ts = now;
    }

    const delta: WorldDelta = {
      kind: 'delta',
      server_time: now,
      upserts: this.vehicles.map((v) => ({
        entity_type: 'vehicle' as const,
        entity_id: v.actor_id,
        data: { ...v },
      })),
      deletes: [],
    };
    useWorldStore.getState().applyDelta(delta);

    if (this.tickCount % 4 === 0) {
      useWorldStore.getState().updateMetrics(generateMetrics());
    }

    if (this.tickCount % 6 === 0) {
      const risk = generateRisk(this.tickCount, this.vehicles);
      if (risk) {
        useWorldStore.getState().upsertEntity({
          entity_type: 'risk',
          entity_id: risk.risk_id,
          data: risk,
        });
        if (Math.random() > 0.4) {
          useAlertStore.getState().upsertAlert(generateAlert(risk));
        }
      }
    }

    if (this.tickCount % 20 === 0) {
      useAlertStore.getState().removeExpired();
    }
  }

  stop(): void {
    if (this.interval) {
      clearInterval(this.interval);
      this.interval = null;
    }
  }
}
