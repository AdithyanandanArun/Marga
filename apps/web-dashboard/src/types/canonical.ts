export type ActorType = 'CAR' | 'BIKE' | 'AUTO' | 'BUS' | 'TRUCK' | 'AMBULANCE' | 'OTHER';
export type ActorSource = 'SIMULATION' | 'PHONE' | 'OBU' | 'RSU' | 'VEHICLE_API';
export type ActorLifecycle = 'NEW' | 'ACTIVE' | 'DEGRADED' | 'STALE' | 'EXPIRED';

export type HazardType =
  | 'POTHOLE' | 'BUMP' | 'DEBRIS' | 'FLOOD' | 'LANDSLIDE'
  | 'ANIMAL' | 'STALLED_VEHICLE' | 'CONSTRUCTION'
  | 'LANE_CLOSURE' | 'ACCIDENT' | 'LOW_VISIBILITY' | 'OTHER';

export type HazardState = 'CANDIDATE' | 'VERIFIED' | 'STALE' | 'EXPIRED';
export type PositionMethod = 'GNSS' | 'FUSED' | 'DEAD_RECKONED' | 'MAP_MATCHED' | 'PEER_AIDED';
export type ConnectivityState = 'FULL' | 'DIRECT_ONLY' | 'INTERMITTENT' | 'ISOLATED';
export type SignalPhaseState = 'RED' | 'AMBER' | 'GREEN';
export type SignalControllerMode = 'FIXED' | 'ACTUATED' | 'EXTERNAL' | 'UNKNOWN';
export type RoadEventType = 'LANE_NARROWING' | 'LANE_CLOSURE' | 'ROAD_CLOSURE' | 'CONSTRUCTION';
export type PedestrianRoadContext = 'SIDEWALK' | 'CROSSWALK' | 'ROADWAY' | 'UNKNOWN';
export type DynamicActorClass = 'ANIMAL' | 'DEBRIS' | 'UNKNOWN_ROAD_USER';
export type AnimalSubtype = 'COW' | 'DOG' | 'ELEPHANT' | 'GOAT' | 'DEER' | 'OTHER';

export type AnimalBehavior =
  | 'NEAR_ROAD' | 'APPROACHING' | 'ENTERING'
  | 'IN_LANE' | 'CROSSING' | 'LEAVING' | 'UNKNOWN';

export interface GeoPosition {
  lat: number;
  lon: number;
  altitude_m?: number;
}

export interface VehicleState {
  schema_version: string;
  actor_id: string;
  actor_type: ActorType;
  ts: string;
  position: GeoPosition;
  position_uncertainty_m: number;
  speed_mps: number;
  acceleration_mps2?: number;
  heading_deg: number;
  yaw_rate_dps?: number;
  road_segment_id?: string;
  lane_id?: string;
  source: ActorSource;
  trust_context_id?: string;
  capabilities: string[];
  lifecycle?: ActorLifecycle;
}

export interface PedestrianState {
  schema_version: string;
  actor_id: string;
  ts: string;
  position: GeoPosition;
  position_uncertainty_m: number;
  speed_mps: number;
  heading_deg: number;
  path_hint?: string;
  road_context: PedestrianRoadContext;
  source: ActorSource;
  confidence: number;
}

export interface Hazard {
  hazard_id: string;
  type: HazardType;
  geometry: GeoJSON.Geometry;
  severity: number;
  confidence: number;
  first_seen: string;
  last_seen: string;
  ttl_s: number;
  source_ids: string[];
  evidence_count: number;
  state: HazardState;
}

export interface PositionEstimate {
  actor_id: string;
  ts: string;
  lat: number;
  lon: number;
  covariance_2d?: [[number, number], [number, number]];
  uncertainty_radius_m: number;
  confidence: number;
  method: PositionMethod;
  evidence: string[];
}

export interface TrafficSignalPhase {
  movement_id: string;
  state: SignalPhaseState;
}

export interface TrafficSignalState {
  signal_id: string;
  junction_id?: string;
  intersection_id?: string;
  ts: string;
  phases?: TrafficSignalPhase[];
  current_phase?: string;
  phase_remaining_s?: number | null;
  movements?: Record<string, SignalPhaseState>;
  controller_mode?: SignalControllerMode;
  source: ActorSource;
  confidence: number;
  position: { lat: number; lon: number };
}

export interface RoadEvent {
  event_id: string;
  type: RoadEventType;
  geometry: GeoJSON.Geometry;
  affected_segment_ids: string[];
  affected_lane_ids: string[];
  effective_from: string;
  effective_until?: string;
  severity: number;
  confidence: number;
  source: ActorSource;
}

export interface DynamicActorObservation {
  observation_id: string;
  actor_class: DynamicActorClass;
  subtype?: AnimalSubtype;
  ts: string;
  position: GeoPosition;
  position_uncertainty_m: number;
  velocity_vector?: { vx: number; vy: number };
  heading_deg?: number;
  detector_confidence: number;
  source_id: string;
  source_type: ActorSource;
  behavior?: AnimalBehavior;
}

export interface RSUState {
  rsu_id: string;
  position: GeoPosition;
  coverage_m: number;
  capabilities: string[];
  link_state: ConnectivityState;
  trust_identity: string;
  last_heartbeat: string;
  observed_actors?: string[];
  observed_hazards?: string[];
}

export interface RiskEvent {
  risk_id: string;
  type: string;
  ts: string;
  affected_actor_ids: string[];
  time_to_conflict_s: number;
  min_predicted_distance_m: number;
  severity: number;
  confidence: number;
  risk_score: number;
  evidence: RiskEvidence[];
  expires_at: string;
}

export interface RiskEvidence {
  entity_id: string;
  entity_type: string;
  metric: string;
  value: number;
  unit: string;
}

export interface Alert {
  alert_id: string;
  risk_id?: string;
  ts: string;
  severity: 'CRITICAL' | 'HIGH' | 'MEDIUM' | 'LOW' | 'INFO';
  title: string;
  description: string;
  affected_actor_ids: string[];
  position?: GeoPosition;
  confidence: number;
  evidence: RiskEvidence[];
  state: 'ACTIVE' | 'CLEARING' | 'CLEARED';
  issued_at: string;
  cleared_at?: string;
  policy_version?: string;
}

export interface DecisionTrace {
  decision_id: string;
  ts: string;
  decision_type: string;
  inputs: { entity_id: string; version: number; timestamp: string }[];
  derived_metrics: Record<string, number>;
  rules_fired: string[];
  output_ids: string[];
  trace_id: string;
}

export interface SystemMetrics {
  actor_updates_per_sec: number;
  event_bus_lag_ms: number;
  risk_evaluations_per_sec: number;
  risk_p50_ms: number;
  risk_p95_ms: number;
  risk_p99_ms: number;
  alerts_issued: number;
  alerts_cleared: number;
  position_uncertainty_avg: number;
  dropped_messages: number;
  ws_clients: number;
  ws_bytes_per_sec: number;
  trust_rejections: number;
  connectivity_state: ConnectivityState;
  simulation_speed: number;
}

export interface Scenario {
  scenario_id: string;
  name: string;
  map_region: string;
  random_seed: number;
  duration_s: number;
  demand_profile: string;
  actors: ScenarioActor[];
  scheduled_events: ScenarioEvent[];
  network_profile: string;
  gps_profile: string;
  assertions: string[];
}

export interface ScenarioActor {
  actor_id: string;
  actor_type: ActorType | DynamicActorClass;
  start_position: GeoPosition;
  route?: string;
  behavior?: string;
}

export interface ScenarioEvent {
  time_s: number;
  event_type: string;
  params: Record<string, unknown>;
}
