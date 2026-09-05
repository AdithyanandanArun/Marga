import { create } from 'zustand';
import type { RouteChange } from '../types/routes';

interface RouteState {
  changesByVehicle: Map<string, RouteChange>;
  connected: boolean;

  upsertChange: (change: RouteChange) => void;
  setConnected: (connected: boolean) => void;
  clear: () => void;
}

export const useRouteStore = create<RouteState>((set) => ({
  changesByVehicle: new Map(),
  connected: false,

  upsertChange: (change) =>
    set((state) => {
      const next = new Map(state.changesByVehicle);
      next.set(change.vehicle_id, change);
      return { changesByVehicle: next };
    }),

  setConnected: (connected) => set({ connected }),

  clear: () => set({ changesByVehicle: new Map() }),
}));
