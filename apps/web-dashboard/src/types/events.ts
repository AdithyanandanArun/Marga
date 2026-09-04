import type {
  VehicleState,
  PedestrianState,
  Hazard,
  RiskEvent,
  Alert,
  TrafficSignalState,
  RoadEvent,
  DynamicActorObservation,
  RSUState,
  SystemMetrics,
} from './canonical';

export interface EventEnvelope<T = unknown> {
  event_id: string;
  event_type: EventType;
  schema_version: string;
  produced_at: string;
  source_service: string;
  correlation_id?: string;
  actor_id?: string;
  trace_id?: string;
  payload: T;
}

export type EventType =
  | 'actor.state.updated'
  | 'infrastructure.signal.updated'
  | 'hazard.observed'
  | 'hazard.updated'
  | 'position.estimate.updated'
  | 'trust.assessment.updated'
  | 'risk.detected'
  | 'alert.issued'
  | 'alert.cleared'
  | 'connectivity.changed'
  | 'road.event.updated'
  | 'system.failure.injected'
  | 'system.metrics';

export interface WorldDelta {
  kind: 'snapshot' | 'delta';
  server_time: string;
  upserts: WorldEntity[];
  deletes: DeletedEntity[];
}

export type WorldEntityType =
  | 'vehicle' | 'pedestrian' | 'hazard' | 'signal'
  | 'road_event' | 'dynamic_actor' | 'rsu' | 'risk';

export interface WorldEntity {
  entity_type: WorldEntityType;
  entity_id: string;
  data: VehicleState | PedestrianState | Hazard | TrafficSignalState
    | RoadEvent | DynamicActorObservation | RSUState | RiskEvent;
}

export interface DeletedEntity {
  entity_type: WorldEntityType;
  entity_id: string;
}

export interface AlertStreamMessage {
  kind: 'alert';
  alert: Alert;
}

export interface MetricsStreamMessage {
  kind: 'metrics';
  metrics: SystemMetrics;
}

export type StreamMessage = WorldDelta | AlertStreamMessage | MetricsStreamMessage;
