// RL dynamic signals contract, owned by Amritha. Ali only consumes it to
// render queues / decision / outcome — the policy and safety controller are
// entirely her side.

export type SignalAction = 'HOLD' | 'EXTEND_GREEN_5' | 'EXTEND_GREEN_10' | 'NEXT_PHASE';

export interface ApproachState {
  approach_id: string;
  queue_length: number;
  lane_density: number;
  avg_speed_mps: number;
  incoming_flow: number;
  downstream_occupancy: number;
  pedestrian_demand: number;
  vru_density: number;
}

export interface SignalOutcome {
  queue_before: number;
  queue_after: number;
  wait_time_before_s?: number;
  wait_time_after_s?: number;
}

export interface SignalRecommendation {
  junction_id: string;
  action: SignalAction;
  confidence: number;
  reason: string;
  safety_checked: boolean;
  proposed_at: string;
}

export interface SignalApplyResult {
  junction_id: string;
  applied_action: SignalAction;
  applied_at: string;
  outcome?: SignalOutcome;
}

export interface SignalJunctionState {
  junction_id: string;
  current_phase: string;
  phase_duration_s: number;
  approaches: ApproachState[];
  last_recommendation?: SignalRecommendation;
  last_outcome?: SignalOutcome;
}
