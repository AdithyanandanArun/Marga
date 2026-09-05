// Edge V2X / PC5 contract, owned by Hrishi. Ali only renders evidence that
// local delivery keeps working — the transport and risk prioritization are
// entirely his side.

export type ConflictType =
  | 'INTERSECTION' | 'HEAD_ON' | 'REAR_END' | 'SIDE_SWIPE'
  | 'EMERGENCY_BRAKING' | 'VRU_CONFLICT';

export interface V2XMessage {
  message_id: string;
  from_node_id: string;
  to_node_id?: string;
  priority: number;
  payload: Record<string, unknown>;
  sent_at: string;
  transport: 'PC5' | string;
}

export interface RiskCreated {
  risk_id: string;
  conflict_type: ConflictType;
  collision_probability: number;
  ttc_s: number;
  uncertainty: number;
  consequence: number;
  vulnerability: number;
  priority_score: number;
  affected_node_ids: string[];
  created_at: string;
}

export interface NodeNeighbour {
  node_id: string;
  distance_m: number;
  link_quality: number;
}

export type TransportState = 'CONNECTED' | 'DEGRADED' | 'DISCONNECTED';

export interface NodeConnectivity {
  node_id: string;
  transport_state: TransportState;
  link_quality: number;
  cloud_reachable: boolean;
  pc5_active: boolean;
}
