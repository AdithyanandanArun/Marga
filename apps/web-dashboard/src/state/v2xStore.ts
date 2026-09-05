import { create } from 'zustand';
import type { NodeConnectivity, RiskCreated, V2XMessage } from '../types/v2x';

const MAX_RECENT_MESSAGES = 50;

interface V2XState {
  recentMessages: V2XMessage[];
  activeRisks: Map<string, RiskCreated>;
  connectivityByNode: Map<string, NodeConnectivity>;
  streamConnected: boolean;

  recordMessage: (message: V2XMessage) => void;
  recordRisk: (risk: RiskCreated) => void;
  setNodeConnectivity: (connectivity: NodeConnectivity) => void;
  setStreamConnected: (connected: boolean) => void;
  clear: () => void;
}

export const useV2XStore = create<V2XState>((set) => ({
  recentMessages: [],
  activeRisks: new Map(),
  connectivityByNode: new Map(),
  streamConnected: false,

  recordMessage: (message) =>
    set((state) => ({ recentMessages: [message, ...state.recentMessages].slice(0, MAX_RECENT_MESSAGES) })),

  recordRisk: (risk) =>
    set((state) => {
      const next = new Map(state.activeRisks);
      next.set(risk.risk_id, risk);
      return { activeRisks: next };
    }),

  setNodeConnectivity: (connectivity) =>
    set((state) => {
      const next = new Map(state.connectivityByNode);
      next.set(connectivity.node_id, connectivity);
      return { connectivityByNode: next };
    }),

  setStreamConnected: (streamConnected) => set({ streamConnected }),

  clear: () => set({ recentMessages: [], activeRisks: new Map(), connectivityByNode: new Map() }),
}));

/** True the moment any node reports an active, cloud-independent PC5 link —
 * the concrete, checkable evidence behind "PC5 links stay local." */
export function selectPc5Local(state: V2XState): boolean {
  return Array.from(state.connectivityByNode.values()).some((c) => c.pc5_active);
}
