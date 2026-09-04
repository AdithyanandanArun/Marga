import { create } from 'zustand';
import type { Alert } from '../types/canonical';

const SEVERITY_ORDER: Record<string, number> = {
  CRITICAL: 0,
  HIGH: 1,
  MEDIUM: 2,
  LOW: 3,
  INFO: 4,
};

interface AlertState {
  alerts: Map<string, Alert>;
  sorted: Alert[];
  selectedAlertId: string | null;

  upsertAlert: (alert: Alert) => void;
  clearAlert: (alertId: string) => void;
  selectAlert: (alertId: string | null) => void;
  removeExpired: () => void;
}

function sortAlerts(alerts: Map<string, Alert>): Alert[] {
  return Array.from(alerts.values())
    .filter((a) => a.state !== 'CLEARED')
    .sort((a, b) => {
      const sevDiff = (SEVERITY_ORDER[a.severity] ?? 5) - (SEVERITY_ORDER[b.severity] ?? 5);
      if (sevDiff !== 0) return sevDiff;
      return new Date(b.issued_at).getTime() - new Date(a.issued_at).getTime();
    });
}

export const useAlertStore = create<AlertState>((set) => ({
  alerts: new Map(),
  sorted: [],
  selectedAlertId: null,

  upsertAlert: (alert) =>
    set((state) => {
      const next = new Map(state.alerts);
      next.set(alert.alert_id, alert);
      return { alerts: next, sorted: sortAlerts(next) };
    }),

  clearAlert: (alertId) =>
    set((state) => {
      const next = new Map(state.alerts);
      const existing = next.get(alertId);
      if (existing) {
        next.set(alertId, { ...existing, state: 'CLEARED', cleared_at: new Date().toISOString() });
      }
      return { alerts: next, sorted: sortAlerts(next) };
    }),

  selectAlert: (alertId) => set({ selectedAlertId: alertId }),

  removeExpired: () =>
    set((state) => {
      const next = new Map(state.alerts);
      const now = Date.now();
      for (const [id, alert] of next) {
        if (alert.state === 'CLEARED' && alert.cleared_at) {
          const clearedAt = new Date(alert.cleared_at).getTime();
          if (now - clearedAt > 30_000) next.delete(id);
        }
      }
      return { alerts: next, sorted: sortAlerts(next) };
    }),
}));
