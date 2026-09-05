// Mobility graph contract, owned by Adithyan1 — mirrors
// packages/schemas/mobility_graph.py (MobilityEdgeState / MobilityIntersectionState)
// exactly. Ali only consumes this shape — never invents a parallel type.

export interface RollingEdgeMetrics {
  window_s: number;
  sample_count: number;
  avg_vehicle_count: number;
  avg_speed_mps: number;
  avg_queue_length: number;
  avg_occupancy: number;
  flow_rate_vph: number;
}

export interface GraphEdgeMetrics {
  schema_version: string;
  edge_id: string;
  ts: string;
  intersection_id: string | null;
  lane_count: number;
  capacity_vehicles: number;
  vehicle_count: number;
  pedestrian_count: number;
  density: number;
  two_wheeler_ratio: number;
  avg_speed_mps: number;
  queue_length: number;
  flow_rate_vph: number;
  occupancy: number;
  capacity_ratio: number;
  hazard_penalty: number;
  gps_confidence: number;
  downstream_congestion: number;
  rolling_windows: Record<string, RollingEdgeMetrics>;
  confidence: number;
  evidence: Record<string, unknown>[];
  provenance: string[];
}

export interface GraphIntersection {
  schema_version: string;
  intersection_id: string;
  ts: string;
  edge_ids: string[];
  vehicle_count: number;
  pedestrian_count: number;
  avg_speed_mps: number;
  queue_length: number;
  occupancy: number;
  downstream_congestion: number;
  gps_confidence: number;
  confidence: number;
  evidence: Record<string, unknown>[];
  provenance: string[];
}

// The service's own envelope (services/mobility_graph/api.py `stream_graph`):
// {"event_type": "graph.edge.updated" | "graph.intersection.updated" | "graph.ping", "data": {...}}
export interface GraphStreamMessage {
  event_type: string;
  data: Record<string, unknown>;
}
