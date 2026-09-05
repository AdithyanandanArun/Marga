import type { RouteChange } from '../types/routes';
import { useRouteStore } from '../state/routeStore';
import { ReconnectingSocket } from './reconnectingSocket';

// Per final_imp.md:
//   POST /routes/recalculate
//   GET  /routes/:vehicle
//   WS   route.changed
// Namespaced under /v1 to match every other gateway route in this app.

export async function recalculateRoute(vehicleId: string): Promise<RouteChange | null> {
  try {
    const res = await fetch('/v1/routes/recalculate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ vehicle_id: vehicleId }),
    });
    if (!res.ok) return null;
    return (await res.json()) as RouteChange;
  } catch {
    return null;
  }
}

export async function fetchVehicleRoute(vehicleId: string): Promise<RouteChange | null> {
  try {
    const res = await fetch(`/v1/routes/${encodeURIComponent(vehicleId)}`);
    if (!res.ok) return null;
    return (await res.json()) as RouteChange;
  } catch {
    return null;
  }
}

export class RouteStream {
  private socket: ReconnectingSocket;

  constructor() {
    const wsBase = `ws://${window.location.host}`;
    this.socket = new ReconnectingSocket({
      url: `${wsBase}/v1/stream/routes`,
      onMessage: (data) => this.handleMessage(data),
      onConnectionChange: (connected) => useRouteStore.getState().setConnected(connected),
    });
  }

  connect(): void {
    this.socket.connect();
  }

  disconnect(): void {
    this.socket.disconnect();
    useRouteStore.getState().setConnected(false);
  }

  private handleMessage(data: string): void {
    try {
      const event = JSON.parse(data) as RouteChange | { event_type?: string; data?: RouteChange };
      const change = 'data' in event && event.data ? event.data : event as RouteChange;
      if (change.vehicle_id) useRouteStore.getState().upsertChange(change);
    } catch {
      // routing service not ready or sent garbage — never fabricate a route.
    }
  }
}
