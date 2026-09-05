import type { RiskEvent } from '../types/canonical';

const DEMO_CONFLICT = new Set(['ego_auto', 'conflict_bus']);

/** Choose one explainable conflict for the overview without fabricating it.
 * The deterministic Bangalore scenario foregrounds its auto–bus interaction;
 * arbitrary deployments still fall back to the strongest live risk score. */
export function selectPrimaryRisk(risks: Iterable<RiskEvent>): RiskEvent | undefined {
  return [...risks].sort((a, b) => {
    const aIsDemoConflict = a.affected_actor_ids.length === DEMO_CONFLICT.size && a.affected_actor_ids.every((id) => DEMO_CONFLICT.has(id));
    const bIsDemoConflict = b.affected_actor_ids.length === DEMO_CONFLICT.size && b.affected_actor_ids.every((id) => DEMO_CONFLICT.has(id));
    if (aIsDemoConflict !== bIsDemoConflict) return aIsDemoConflict ? -1 : 1;
    return b.risk_score - a.risk_score || a.time_to_conflict_s - b.time_to_conflict_s;
  })[0];
}
