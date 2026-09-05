// Cooperative routing contract, owned by Adithyan2. Ali only renders the
// old/new route, the ETA change, and the stated reason.

export interface RouteGeometryPoint {
  lat: number;
  lon: number;
}

export type RerouteReason = 'material_eta_improvement' | 'critical_hazard' | 'closure' | string;

export interface RouteChange {
  vehicle_id: string;
  old_route: RouteGeometryPoint[];
  new_route: RouteGeometryPoint[];
  old_eta_s: number;
  new_eta_s: number;
  reason: RerouteReason;
  changed_at: string;
}
