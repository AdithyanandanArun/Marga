import { create } from 'zustand';
import type {
  VehicleState,
  PedestrianState,
  Hazard,
  TrafficSignalState,
  RoadEvent,
  DynamicActorObservation,
  RSUState,
  RiskEvent,
  SystemMetrics,
  ConnectivityState,
} from '../types/canonical';
import type { WorldDelta, WorldEntity, DeletedEntity } from '../types/events';

interface WorldState {
  vehicles: Map<string, VehicleState>;
  pedestrians: Map<string, PedestrianState>;
  hazards: Map<string, Hazard>;
  signals: Map<string, TrafficSignalState>;
  roadEvents: Map<string, RoadEvent>;
  dynamicActors: Map<string, DynamicActorObservation>;
  rsus: Map<string, RSUState>;
  risks: Map<string, RiskEvent>;
  metrics: SystemMetrics | null;
  connectivity: ConnectivityState;
  lastUpdate: string | null;

  applyDelta: (delta: WorldDelta) => void;
  upsertEntity: (entity: WorldEntity) => void;
  deleteEntity: (del: DeletedEntity) => void;
  updateMetrics: (metrics: SystemMetrics) => void;
  setConnectivity: (state: ConnectivityState) => void;
  clear: () => void;
}

export const useWorldStore = create<WorldState>((set, get) => ({
  vehicles: new Map(),
  pedestrians: new Map(),
  hazards: new Map(),
  signals: new Map(),
  roadEvents: new Map(),
  dynamicActors: new Map(),
  rsus: new Map(),
  risks: new Map(),
  metrics: null,
  connectivity: 'FULL',
  lastUpdate: null,

  applyDelta: (delta) => {
    set((state) => {
      const vehicles = delta.kind === 'snapshot' ? new Map<string, VehicleState>() : new Map(state.vehicles);
      const pedestrians = delta.kind === 'snapshot' ? new Map<string, PedestrianState>() : new Map(state.pedestrians);
      const hazards = delta.kind === 'snapshot' ? new Map<string, Hazard>() : new Map(state.hazards);
      const signals = delta.kind === 'snapshot' ? new Map<string, TrafficSignalState>() : new Map(state.signals);
      const roadEvents = delta.kind === 'snapshot' ? new Map<string, RoadEvent>() : new Map(state.roadEvents);
      const dynamicActors = delta.kind === 'snapshot' ? new Map<string, DynamicActorObservation>() : new Map(state.dynamicActors);
      const rsus = delta.kind === 'snapshot' ? new Map<string, RSUState>() : new Map(state.rsus);
      const risks = delta.kind === 'snapshot' ? new Map<string, RiskEvent>() : new Map(state.risks);

      for (const entity of delta.upserts) {
        switch (entity.entity_type) {
          case 'vehicle': vehicles.set(entity.entity_id, entity.data as VehicleState); break;
          case 'pedestrian': pedestrians.set(entity.entity_id, entity.data as PedestrianState); break;
          case 'hazard': hazards.set(entity.entity_id, entity.data as Hazard); break;
          case 'signal': signals.set(entity.entity_id, entity.data as TrafficSignalState); break;
          case 'road_event': roadEvents.set(entity.entity_id, entity.data as RoadEvent); break;
          case 'dynamic_actor': dynamicActors.set(entity.entity_id, entity.data as DynamicActorObservation); break;
          case 'rsu': rsus.set(entity.entity_id, entity.data as RSUState); break;
          case 'risk': risks.set(entity.entity_id, entity.data as RiskEvent); break;
        }
      }
      for (const del of delta.deletes) {
        switch (del.entity_type) {
          case 'vehicle': vehicles.delete(del.entity_id); break;
          case 'pedestrian': pedestrians.delete(del.entity_id); break;
          case 'hazard': hazards.delete(del.entity_id); break;
          case 'signal': signals.delete(del.entity_id); break;
          case 'road_event': roadEvents.delete(del.entity_id); break;
          case 'dynamic_actor': dynamicActors.delete(del.entity_id); break;
          case 'rsu': rsus.delete(del.entity_id); break;
          case 'risk': risks.delete(del.entity_id); break;
        }
      }

      return { vehicles, pedestrians, hazards, signals, roadEvents, dynamicActors, rsus, risks, lastUpdate: delta.server_time };
    });
  },

  upsertEntity: (entity) => {
    set((state) => {
      switch (entity.entity_type) {
        case 'vehicle': {
          const next = new Map(state.vehicles);
          next.set(entity.entity_id, entity.data as VehicleState);
          return { vehicles: next };
        }
        case 'pedestrian': {
          const next = new Map(state.pedestrians);
          next.set(entity.entity_id, entity.data as PedestrianState);
          return { pedestrians: next };
        }
        case 'hazard': {
          const next = new Map(state.hazards);
          next.set(entity.entity_id, entity.data as Hazard);
          return { hazards: next };
        }
        case 'signal': {
          const next = new Map(state.signals);
          next.set(entity.entity_id, entity.data as TrafficSignalState);
          return { signals: next };
        }
        case 'road_event': {
          const next = new Map(state.roadEvents);
          next.set(entity.entity_id, entity.data as RoadEvent);
          return { roadEvents: next };
        }
        case 'dynamic_actor': {
          const next = new Map(state.dynamicActors);
          next.set(entity.entity_id, entity.data as DynamicActorObservation);
          return { dynamicActors: next };
        }
        case 'rsu': {
          const next = new Map(state.rsus);
          next.set(entity.entity_id, entity.data as RSUState);
          return { rsus: next };
        }
        case 'risk': {
          const next = new Map(state.risks);
          next.set(entity.entity_id, entity.data as RiskEvent);
          return { risks: next };
        }
        default:
          return {};
      }
    });
  },

  deleteEntity: (del) => {
    set((state) => {
      switch (del.entity_type) {
        case 'vehicle': {
          const next = new Map(state.vehicles);
          next.delete(del.entity_id);
          return { vehicles: next };
        }
        case 'pedestrian': {
          const next = new Map(state.pedestrians);
          next.delete(del.entity_id);
          return { pedestrians: next };
        }
        case 'hazard': {
          const next = new Map(state.hazards);
          next.delete(del.entity_id);
          return { hazards: next };
        }
        case 'signal': {
          const next = new Map(state.signals);
          next.delete(del.entity_id);
          return { signals: next };
        }
        case 'road_event': {
          const next = new Map(state.roadEvents);
          next.delete(del.entity_id);
          return { roadEvents: next };
        }
        case 'dynamic_actor': {
          const next = new Map(state.dynamicActors);
          next.delete(del.entity_id);
          return { dynamicActors: next };
        }
        case 'rsu': {
          const next = new Map(state.rsus);
          next.delete(del.entity_id);
          return { rsus: next };
        }
        case 'risk': {
          const next = new Map(state.risks);
          next.delete(del.entity_id);
          return { risks: next };
        }
        default:
          return {};
      }
    });
  },

  updateMetrics: (metrics) => set({ metrics }),
  setConnectivity: (connectivity) => set({ connectivity }),

  clear: () =>
    set({
      vehicles: new Map(),
      pedestrians: new Map(),
      hazards: new Map(),
      signals: new Map(),
      roadEvents: new Map(),
      dynamicActors: new Map(),
      rsus: new Map(),
      risks: new Map(),
    }),
}));
