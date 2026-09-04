import { useState, useCallback, useEffect, useRef } from 'react';
import { Link } from 'react-router-dom';
import { MapView } from '../map/MapView';
import { useWorldStore } from '../state/worldStore';
import type { VehicleState } from '../types/canonical';
import type { WorldEntity } from '../types/events';
import {
  ArrowLeft,
  Play,
  Pause,
  SkipBack,
  SkipForward,
  Rewind,
  FastForward,
  Clock,
  FileText,
  ChevronDown,
  ChevronRight,
  AlertTriangle,
  Zap,
  Target,
  Radio,
} from 'lucide-react';

const GATEWAY = '';
const SPEEDS = [0.25, 0.5, 1, 2, 4];

interface Run {
  run_id: string;
  scenario_id: string;
  state: string;
  started_at: string | null;
  current_sim_time_s: number;
}

interface RecordedEvent {
  sim_time_s: number;
  event_type: string;
  payload: Record<string, unknown>;
}

function eventToVehicle(ev: RecordedEvent): VehicleState | null {
  if (ev.event_type !== 'actor.state.updated') return null;
  const p = ev.payload as Record<string, unknown>;
  const pos = p.position as Record<string, number> | null;
  if (!pos) return null;
  const actorId = (p.vehicle_id ?? p.actor_id) as string | undefined;
  if (!actorId) return null;
  return {
    actor_id: actorId,
    actor_type: ((p.vehicle_type ?? p.actor_type ?? 'CAR') as string).toUpperCase() as VehicleState['actor_type'],
    ts: (p.timestamp_utc ?? new Date().toISOString()) as string,
    position: { lat: pos.lat, lon: pos.lon },
    position_uncertainty_m: (pos.uncertainty_m as number | undefined) ?? 2.0,
    speed_mps: (p.speed_mps as number) ?? 0,
    acceleration_mps2: (p.acceleration_mps2 as number | undefined) ?? null,
    heading_deg: (p.heading_deg as number) ?? 0,
    road_segment_id: null,
    lane_id: null,
    source: 'SIMULATION',
    capabilities: [],
  };
}

export function ReplayView() {
  const [runs, setRuns] = useState<Run[]>([]);
  const [selectedRunId, setSelectedRunId] = useState<string>('');
  const [events, setEvents] = useState<RecordedEvent[]>([]);
  const [duration, setDuration] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(1);
  const [currentTime, setCurrentTime] = useState(0);
  const [loading, setLoading] = useState(false);
  const [expandedSections, setExpandedSections] = useState<Set<string>>(new Set(['stats', 'actors']));
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const upsertEntity = useWorldStore((s) => s.upsertEntity);
  const clear = useWorldStore((s) => s.clear);

  // Fetch available runs on mount
  useEffect(() => {
    fetch(`${GATEWAY}/v1/replay/runs`)
      .then((r) => r.json())
      .then((data: Run[]) => {
        setRuns(data);
        if (data.length > 0) setSelectedRunId(data[0].run_id);
      })
      .catch(() => {});
  }, []);

  // Fetch events when run changes
  useEffect(() => {
    if (!selectedRunId) return;
    setLoading(true);
    setPlaying(false);
    setCurrentTime(0);
    clear();
    fetch(`${GATEWAY}/v1/replay/${selectedRunId}/events?limit=50000`)
      .then((r) => r.json())
      .then((data: { events?: RecordedEvent[]; run?: Run }) => {
        const evs = data.events ?? [];
        setEvents(evs);
        const maxT = evs.reduce((m, e) => Math.max(m, e.sim_time_s), 0);
        setDuration(maxT || (data.run?.current_sim_time_s ?? 60));
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [selectedRunId]);

  // Inject world state at currentTime
  useEffect(() => {
    if (events.length === 0) return;
    // Build last-known state for each actor up to currentTime
    const actorMap = new Map<string, VehicleState>();
    for (const ev of events) {
      if (ev.sim_time_s > currentTime) break;
      const v = eventToVehicle(ev);
      if (v) actorMap.set(v.actor_id, v);
    }
    for (const [id, v] of actorMap) {
      upsertEntity({ entity_type: 'vehicle', entity_id: id, data: v } as WorldEntity);
    }
  }, [currentTime, events]);

  // Playback ticker
  useEffect(() => {
    if (intervalRef.current) clearInterval(intervalRef.current);
    if (!playing) return;
    const TICK_MS = 100;
    intervalRef.current = setInterval(() => {
      setCurrentTime((t) => {
        const next = t + (TICK_MS / 1000) * speed;
        if (next >= duration) {
          setPlaying(false);
          return duration;
        }
        return next;
      });
    }, TICK_MS);
    return () => { if (intervalRef.current) clearInterval(intervalRef.current); };
  }, [playing, speed, duration]);

  const toggleSection = (section: string) => {
    setExpandedSections((prev) => {
      const next = new Set(prev);
      if (next.has(section)) next.delete(section); else next.add(section);
      return next;
    });
  };

  const handleTimeChange = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    setCurrentTime(parseFloat(e.target.value));
  }, []);

  const selectedRun = runs.find((r) => r.run_id === selectedRunId);
  const actorsAtTime = new Map<string, VehicleState>();
  for (const ev of events) {
    if (ev.sim_time_s > currentTime) break;
    const v = eventToVehicle(ev);
    if (v) actorsAtTime.set(v.actor_id, v);
  }

  return (
    <div style={replayStyles.container}>
      <header style={replayStyles.header}>
        <Link to="/" style={replayStyles.backLink}><ArrowLeft size={18} /> Control Center</Link>
        <div style={replayStyles.headerCenter}>
          <Clock size={18} style={{ color: 'var(--accent-cyan)' }} />
          <span style={{ fontWeight: 700, fontSize: 15 }}>Scenario Replay</span>
        </div>
        <div style={replayStyles.runSelect}>
          {runs.length === 0 ? (
            <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>
              {loading ? 'Loading…' : 'No runs — start a scenario first'}
            </span>
          ) : (
            <>
              <select
                value={selectedRunId}
                onChange={(e) => setSelectedRunId(e.target.value)}
                style={replayStyles.select}
              >
                {runs.map((r) => (
                  <option key={r.run_id} value={r.run_id}>
                    {r.run_id.slice(0, 8)} — {r.state} ({r.current_sim_time_s.toFixed(0)}s)
                  </option>
                ))}
              </select>
              <ChevronDown size={14} style={{ position: 'absolute', right: 8, top: '50%', transform: 'translateY(-50%)', color: 'var(--text-muted)', pointerEvents: 'none' }} />
            </>
          )}
        </div>
      </header>

      <div style={replayStyles.main}>
        <div style={replayStyles.mapSection}>
          <MapView />
        </div>

        <aside style={replayStyles.evidenceSidebar}>
          <h3 style={replayStyles.sidebarTitle}><FileText size={14} /> Run Info</h3>

          <Section title="Run Stats" icon={<Target size={14} />} expanded={expandedSections.has('stats')} onToggle={() => toggleSection('stats')}>
            <div style={replayStyles.metricRow}>
              <span style={replayStyles.metricLabel}>Run ID</span>
              <span style={replayStyles.metricValue}>{selectedRunId.slice(0, 8) || '—'}</span>
            </div>
            <div style={replayStyles.metricRow}>
              <span style={replayStyles.metricLabel}>State</span>
              <span style={replayStyles.metricValue}>{selectedRun?.state ?? '—'}</span>
            </div>
            <div style={replayStyles.metricRow}>
              <span style={replayStyles.metricLabel}>Total Events</span>
              <span style={replayStyles.metricValue}>{events.length.toLocaleString()}</span>
            </div>
            <div style={replayStyles.metricRow}>
              <span style={replayStyles.metricLabel}>Duration</span>
              <span style={replayStyles.metricValue}>{duration.toFixed(1)}s</span>
            </div>
          </Section>

          <Section title="Actors at Time" icon={<Radio size={14} />} expanded={expandedSections.has('actors')} onToggle={() => toggleSection('actors')}>
            {actorsAtTime.size === 0 ? (
              <div style={{ color: 'var(--text-muted)', fontSize: 12 }}>No actors yet</div>
            ) : (
              Array.from(actorsAtTime.values()).slice(0, 8).map((v) => (
                <div key={v.actor_id} style={replayStyles.traceItem}>
                  <span style={replayStyles.traceId}>{v.actor_id}</span>
                  <span style={replayStyles.traceDetail}>{(v.speed_mps * 3.6).toFixed(0)} km/h</span>
                </div>
              ))
            )}
            {actorsAtTime.size > 8 && (
              <div style={{ fontSize: 11, color: 'var(--text-muted)', paddingTop: 4 }}>
                +{actorsAtTime.size - 8} more
              </div>
            )}
          </Section>

          <Section title="Events Near Cursor" icon={<Zap size={14} />} expanded={expandedSections.has('events')} onToggle={() => toggleSection('events')}>
            {events.filter((e) => Math.abs(e.sim_time_s - currentTime) < 0.5).slice(0, 5).map((e, i) => (
              <div key={i} style={replayStyles.metricRow}>
                <span style={replayStyles.metricLabel}>{e.event_type.split('.').pop()}</span>
                <span style={replayStyles.metricValue}>{e.sim_time_s.toFixed(2)}s</span>
              </div>
            ))}
          </Section>
        </aside>
      </div>

      <div style={replayStyles.controls}>
        <div style={replayStyles.controlsLeft}>
          <button onClick={() => { setCurrentTime(0); setPlaying(false); }} style={replayStyles.controlBtn}><SkipBack size={16} /></button>
          <button onClick={() => setCurrentTime((t) => Math.max(0, t - 1))} style={replayStyles.controlBtn}><Rewind size={16} /></button>
          <button onClick={() => setPlaying((p) => !p)} style={{ ...replayStyles.controlBtn, ...replayStyles.playBtn }}>
            {playing ? <Pause size={18} /> : <Play size={18} />}
          </button>
          <button onClick={() => setCurrentTime((t) => Math.min(duration, t + 1))} style={replayStyles.controlBtn}><FastForward size={16} /></button>
          <button onClick={() => { setCurrentTime(duration); setPlaying(false); }} style={replayStyles.controlBtn}><SkipForward size={16} /></button>
        </div>

        <div style={replayStyles.timeline}>
          <span style={replayStyles.timeLabel}>{currentTime.toFixed(1)}s</span>
          <input
            type="range" min={0} max={duration} step={0.1} value={currentTime}
            onChange={handleTimeChange} style={replayStyles.slider}
          />
          <span style={replayStyles.timeLabel}>{duration.toFixed(0)}s</span>
        </div>

        <div style={replayStyles.speedControl}>
          {SPEEDS.map((s) => (
            <button key={s} onClick={() => setSpeed(s)} style={{
              ...replayStyles.speedBtn,
              background: speed === s ? 'var(--accent-blue)' : 'var(--bg-primary)',
              color: speed === s ? '#fff' : 'var(--text-secondary)',
            }}>{s}x</button>
          ))}
        </div>
      </div>
    </div>
  );
}

function Section({ title, icon, expanded, onToggle, children }: {
  title: string; icon: React.ReactNode; expanded: boolean; onToggle: () => void; children: React.ReactNode;
}) {
  return (
    <div style={replayStyles.section}>
      <button onClick={onToggle} style={replayStyles.sectionHeader}>
        {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        {icon}
        <span>{title}</span>
      </button>
      {expanded && <div style={replayStyles.sectionContent}>{children}</div>}
    </div>
  );
}

const replayStyles: Record<string, React.CSSProperties> = {
  container: { display: 'flex', flexDirection: 'column', height: '100vh', overflow: 'hidden' },
  header: {
    display: 'flex', alignItems: 'center', height: 48, padding: '0 16px',
    background: 'var(--bg-secondary)', borderBottom: '1px solid var(--border-primary)', flexShrink: 0, gap: 12,
  },
  backLink: {
    display: 'flex', alignItems: 'center', gap: 6, color: 'var(--text-secondary)',
    textDecoration: 'none', fontSize: 13, fontWeight: 500,
  },
  headerCenter: { display: 'flex', alignItems: 'center', gap: 8 },
  runSelect: { position: 'relative', marginLeft: 'auto' },
  select: {
    padding: '6px 28px 6px 10px', appearance: 'none',
    background: 'var(--bg-tertiary)', border: '1px solid var(--border-primary)',
    borderRadius: 'var(--radius-md)', color: 'var(--text-primary)', fontSize: 12,
  },
  main: { flex: 1, display: 'flex', overflow: 'hidden' },
  mapSection: { flex: 1, position: 'relative' },
  evidenceSidebar: {
    width: 300, flexShrink: 0, overflow: 'auto', padding: 12,
    background: 'var(--bg-secondary)', borderLeft: '1px solid var(--border-primary)',
  },
  sidebarTitle: {
    display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, fontWeight: 600,
    color: 'var(--text-primary)', marginBottom: 12,
  },
  section: { marginBottom: 4, borderRadius: 'var(--radius-md)', overflow: 'hidden' },
  sectionHeader: {
    display: 'flex', alignItems: 'center', gap: 8, width: '100%', padding: '10px 8px',
    background: 'var(--bg-elevated)', border: 'none', color: 'var(--text-secondary)',
    fontSize: 12, fontWeight: 600, cursor: 'pointer', textTransform: 'uppercase', letterSpacing: 0.3,
  },
  sectionContent: { padding: '8px 12px', background: 'var(--bg-tertiary)' },
  traceItem: {
    display: 'flex', justifyContent: 'space-between', padding: '4px 0',
    borderBottom: '1px solid var(--border-primary)',
  },
  traceId: { fontSize: 12, fontFamily: 'var(--font-mono)', color: 'var(--accent-blue)' },
  traceDetail: { fontSize: 11, color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' },
  metricRow: { display: 'flex', justifyContent: 'space-between', padding: '4px 0' },
  metricLabel: { fontSize: 12, color: 'var(--text-secondary)', textTransform: 'capitalize' },
  metricValue: { fontSize: 12, fontWeight: 600, color: 'var(--text-primary)', fontFamily: 'var(--font-mono)' },
  traceFooter: { padding: '12px 0', borderTop: '1px solid var(--border-primary)', marginTop: 8 },
  controls: {
    display: 'flex', alignItems: 'center', gap: 16, padding: '10px 16px',
    background: 'var(--bg-secondary)', borderTop: '1px solid var(--border-primary)', flexShrink: 0,
  },
  controlsLeft: { display: 'flex', gap: 4 },
  controlBtn: {
    width: 36, height: 36, display: 'flex', alignItems: 'center', justifyContent: 'center',
    background: 'var(--bg-tertiary)', border: '1px solid var(--border-primary)',
    borderRadius: 'var(--radius-md)', color: 'var(--text-primary)', cursor: 'pointer',
  },
  playBtn: {
    width: 44, height: 44, borderRadius: '50%', background: 'var(--accent-blue)',
    border: 'none', color: '#fff',
  },
  timeline: { flex: 1, display: 'flex', alignItems: 'center', gap: 8 },
  timeLabel: { fontSize: 12, color: 'var(--text-muted)', fontFamily: 'var(--font-mono)', width: 48 },
  slider: { flex: 1, accentColor: 'var(--accent-blue)' },
  speedControl: { display: 'flex', gap: 2 },
  speedBtn: {
    padding: '4px 8px', border: 'none', borderRadius: 'var(--radius-sm)',
    fontSize: 11, fontWeight: 600, cursor: 'pointer',
  },
};
