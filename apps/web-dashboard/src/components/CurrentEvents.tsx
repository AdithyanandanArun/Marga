import { useEffect } from 'react';
import { create } from 'zustand';
import { useWorldStore } from '../state/worldStore';
import { useRouteStore } from '../state/routeStore';

interface EventItem {
  id: string;
  time: string;
  title: string;
  detail: string;
  color: string;
}

// Presentation history only: inputs are gateway observations/decisions, never
// locally generated simulation outcomes. Bound both history and dedupe memory.
const seen = new Map<string, string>();
export const useCurrentEvents = create<{ events: EventItem[] }>(() => ({ events: [] }));
export function recordEvent(event: EventItem, revision = event.time) {
  if (seen.get(event.id) === revision) return;
  seen.delete(event.id);
  seen.set(event.id, revision);
  if (seen.size > 512) seen.delete(seen.keys().next().value!);
  useCurrentEvents.setState(({ events }) => ({
    events: [event, ...events.filter((item) => item.id !== event.id)]
      .sort((a, b) => Date.parse(b.time) - Date.parse(a.time)).slice(0, 10),
  }));
}

export function useCurrentEventFeed() {
  useEffect(() => {
    const readWorld = () => {
      const world = useWorldStore.getState();
      for (const risk of world.risks.values()) {
        const pair = [...risk.affected_actor_ids].sort();
        const id = `risk:${risk.type}:${pair.join(':')}`;
        // Repeated TTC estimates update one ongoing episode, not ten cards.
        const prior = seen.get(id);
        const episode = prior && Date.parse(risk.ts) - Date.parse(prior) < 15000 ? prior : risk.ts;
        recordEvent({ id, time: risk.ts,
          title: risk.type === 'INTERSECTION_CONFLICT' ? 'Approaching the same path' : `${risk.type.replace(/_/g, ' ').toLowerCase()} conflict`,
          detail: `${pair.join(' ↔ ')} · TTC ${risk.time_to_conflict_s.toFixed(1)} s`,
          color: 'var(--accent-red)',
        }, episode);
      }
      for (const hazard of world.hazards.values()) {
        if (!['ACCIDENT', 'STALLED_VEHICLE'].includes(hazard.type)) continue;
        recordEvent({ id: `hazard:${hazard.hazard_id}`, time: hazard.first_seen,
          title: hazard.type === 'ACCIDENT' ? 'Collision / accident reported' : 'Vehicle blockage reported',
          detail: `${hazard.hazard_id} · ${hazard.state.toLowerCase()}`,
          color: 'var(--accent-orange, #fb923c)',
        }, hazard.hazard_id);
      }
    };
    const readRoutes = () => {
      for (const route of useRouteStore.getState().changesByVehicle.values()) {
        recordEvent({ id: `route:${route.vehicle_id}:${route.changed_at}`, time: route.changed_at,
          title: 'Suggested reroute',
          detail: `${route.vehicle_id} · ${route.reason.replace(/_/g, ' ')} · ETA ${Math.round(route.old_eta_s)} → ${Math.round(route.new_eta_s)} s`,
          color: 'var(--accent-green)',
        });
      }
    };
    readWorld(); readRoutes();
    const stopWorld = useWorldStore.subscribe((state, prev) => {
      if (state.risks !== prev.risks || state.hazards !== prev.hazards) readWorld();
    });
    const stopRoutes = useRouteStore.subscribe(readRoutes);
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    const readSignals = async () => {
      try {
        const response = await fetch('/v1/signals/commands/history', { signal: controller.signal });
        if (response.ok) {
          const { commands } = await response.json() as { commands: Array<{ signal_id: string; action: string; issued_at: string }> };
          for (const command of commands) recordEvent({
            id: `signal:${command.signal_id}:${command.issued_at}`, time: command.issued_at,
            title: 'Traffic signal timing change',
            detail: `${command.signal_id} · ${command.action.replace(/_/g, ' ')} · controller command issued`,
            color: 'var(--accent-yellow)',
          });
        }
      } catch { /* A disconnected controller does not invent events. */ }
      finally { if (!controller.signal.aborted) timer = setTimeout(readSignals, 3000); }
    };
    void readSignals();
    return () => { stopWorld(); stopRoutes(); controller.abort(); clearTimeout(timer); };
  }, []);
}

export function CurrentEvents({ onClose }: { onClose: () => void }) {
  const events = useCurrentEvents((state) => state.events);
  return <aside id="current-events" aria-label="Current Events" style={{ width: 350, maxWidth: '45vw', flexShrink: 0, overflowY: 'auto', background: 'var(--bg-secondary)', borderLeft: '1px solid var(--border-primary)' }}>
    <header style={{ padding: 16, display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
      <div><h2 style={{ margin: 0, fontSize: 16 }}>Current Events</h2><p style={{ margin: '6px 0 0', fontSize: 12, color: 'var(--text-secondary)' }}>Latest 10 · newest first</p></div>
      <button onClick={onClose} aria-label="Close current events" style={{ background: 'transparent', color: 'var(--text-primary)', border: 0, padding: 10, cursor: 'pointer', fontSize: 22 }}>×</button>
    </header>
    {!events.length && <p style={{ padding: 16, color: 'var(--text-secondary)' }}>Waiting for verified events. No events are fabricated.</p>}
    <ol aria-label="Latest events" style={{ listStyle: 'none', margin: 0, padding: '0 12px 12px' }}>
      {events.map((event) => <li key={event.id} style={{ padding: 12, marginBottom: 8, borderLeft: `3px solid ${event.color}`, background: 'var(--bg-tertiary)', borderRadius: 5 }}>
        <time dateTime={event.time} style={{ fontSize: 10, color: 'var(--text-secondary)' }}>{new Date(event.time).toLocaleTimeString()}</time>
        <h3 style={{ fontSize: 13, margin: '5px 0', textTransform: 'capitalize' }}>{event.title}</h3>
        <p style={{ fontSize: 12, lineHeight: 1.5, margin: 0, color: 'var(--text-secondary)', overflowWrap: 'anywhere' }}>{event.detail}</p>
      </li>)}
    </ol>
  </aside>;
}
