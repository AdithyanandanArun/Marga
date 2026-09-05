import type { SignalAction, SignalApplyResult, SignalJunctionState, SignalRecommendation } from '../types/signals';

// Per final_imp.md:
//   POST /signals/:junction/recommend
//   POST /signals/:junction/apply
//   GET  /signals/:junction/state
// Namespaced under /v1 to match every other gateway route in this app.

export async function fetchSignalState(junctionId: string): Promise<SignalJunctionState | null> {
  try {
    const res = await fetch(`/v1/signals/${encodeURIComponent(junctionId)}/state`);
    if (!res.ok) return null;
    return (await res.json()) as SignalJunctionState;
  } catch {
    return null;
  }
}

export async function requestSignalRecommendation(junctionId: string): Promise<SignalRecommendation | null> {
  try {
    const res = await fetch(`/v1/signals/${encodeURIComponent(junctionId)}/recommend`, { method: 'POST' });
    if (!res.ok) return null;
    return (await res.json()) as SignalRecommendation;
  } catch {
    return null;
  }
}

export async function applySignalAction(junctionId: string, action: SignalAction): Promise<SignalApplyResult | null> {
  try {
    const res = await fetch(`/v1/signals/${encodeURIComponent(junctionId)}/apply`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action }),
    });
    if (!res.ok) return null;
    return (await res.json()) as SignalApplyResult;
  } catch {
    return null;
  }
}
