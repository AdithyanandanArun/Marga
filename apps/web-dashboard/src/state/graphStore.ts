import { create } from 'zustand';
import type { GraphEdgeMetrics, GraphIntersection } from '../types/graph';

interface GraphState {
  edges: Map<string, GraphEdgeMetrics>;
  intersections: Map<string, GraphIntersection>;
  connected: boolean;

  upsertEdge: (edge: GraphEdgeMetrics) => void;
  upsertIntersection: (intersection: GraphIntersection) => void;
  setConnected: (connected: boolean) => void;
  clear: () => void;
}

export const useGraphStore = create<GraphState>((set) => ({
  edges: new Map(),
  intersections: new Map(),
  connected: false,

  upsertEdge: (edge) =>
    set((state) => {
      const next = new Map(state.edges);
      next.set(edge.edge_id, edge);
      return { edges: next };
    }),

  upsertIntersection: (intersection) =>
    set((state) => {
      const next = new Map(state.intersections);
      next.set(intersection.intersection_id, intersection);
      return { intersections: next };
    }),

  setConnected: (connected) => set({ connected }),

  clear: () => set({ edges: new Map(), intersections: new Map() }),
}));
