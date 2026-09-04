import { useState, useCallback } from 'react';
import { Link } from 'react-router-dom';
import { MapView } from '../map/MapView';
import type { Scenario, ScenarioActor, ScenarioEvent } from '../types/canonical';
import {
  ArrowLeft,
  Play,
  Save,
  Plus,
  Trash2,
  Settings,
  Map,
  Clock,
  Users,
  Zap,
  Wifi,
  Satellite,
  FileText,
  FolderOpen,
} from 'lucide-react';

const EMPTY_SCENARIO: Omit<Scenario, 'scenario_id'> = {
  name: 'New Scenario',
  map_region: '',
  random_seed: Math.floor(Math.random() * 999999),
  duration_s: 300,
  demand_profile: 'medium',
  actors: [],
  scheduled_events: [],
  network_profile: 'normal',
  gps_profile: 'good',
  assertions: [],
};

const DEMAND_PRESETS = ['light', 'medium', 'heavy', 'rush-hour'];
const NETWORK_PRESETS = ['normal', 'degraded', 'intermittent', 'isolated'];
const GPS_PRESETS = ['good', 'urban-canyon', 'degraded', 'denied'];
const EVENT_TYPES = [
  'road_closure', 'gps_degradation', 'network_failure',
  'hazard_spawn', 'animal_crossing', 'rsu_failure',
];

export function ScenarioStudio() {
  const [scenario, setScenario] = useState(EMPTY_SCENARIO);
  const [savedScenarios, setSavedScenarios] = useState<Scenario[]>(() => {
    try {
      return JSON.parse(localStorage.getItem('marga_scenarios') ?? '[]');
    } catch { return []; }
  });
  const [showList, setShowList] = useState(false);

  const update = useCallback(<K extends keyof typeof scenario>(key: K, value: (typeof scenario)[K]) => {
    setScenario((s) => ({ ...s, [key]: value }));
  }, []);

  const addActor = () => {
    const actor: ScenarioActor = {
      actor_id: `actor-${Date.now()}`,
      actor_type: 'CAR',
      start_position: { lat: 12.9716, lon: 77.5946 },
    };
    update('actors', [...scenario.actors, actor]);
  };

  const removeActor = (idx: number) => {
    update('actors', scenario.actors.filter((_, i) => i !== idx));
  };

  const addEvent = () => {
    const evt: ScenarioEvent = {
      time_s: 30,
      event_type: 'hazard_spawn',
      params: {},
    };
    update('scheduled_events', [...scenario.scheduled_events, evt]);
  };

  const removeEvent = (idx: number) => {
    update('scheduled_events', scenario.scheduled_events.filter((_, i) => i !== idx));
  };

  const saveScenario = () => {
    const id = `scn-${Date.now()}`;
    const full: Scenario = { scenario_id: id, ...scenario };
    const next = [...savedScenarios, full];
    setSavedScenarios(next);
    try { localStorage.setItem('marga_scenarios', JSON.stringify(next)); } catch {}
  };

  const loadScenario = (s: Scenario) => {
    const { scenario_id: _, ...rest } = s;
    setScenario(rest);
    setShowList(false);
  };

  const deleteScenario = (id: string) => {
    const next = savedScenarios.filter((s) => s.scenario_id !== id);
    setSavedScenarios(next);
    try { localStorage.setItem('marga_scenarios', JSON.stringify(next)); } catch {}
  };

  return (
    <div style={studioStyles.container}>
      <header style={studioStyles.header}>
        <Link to="/" style={studioStyles.backLink}><ArrowLeft size={18} /> Control Center</Link>
        <div style={studioStyles.headerCenter}>
          <Settings size={18} style={{ color: 'var(--accent-purple)' }} />
          <span style={{ fontWeight: 700, fontSize: 15 }}>Scenario Studio</span>
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <button onClick={() => setShowList(!showList)} style={studioStyles.headerBtn}>
            <FolderOpen size={14} /> Load
          </button>
          <button onClick={saveScenario} style={{ ...studioStyles.headerBtn, background: 'var(--accent-blue)', color: '#fff' }}>
            <Save size={14} /> Save
          </button>
        </div>
      </header>

      <div style={studioStyles.main}>
        <aside style={studioStyles.sidebar}>
          {showList ? (
            <div>
              <h3 style={studioStyles.sectionTitle}><FileText size={14} /> Saved Scenarios</h3>
              {savedScenarios.length === 0 ? (
                <p style={{ color: 'var(--text-muted)', fontSize: 12, padding: 12 }}>No saved scenarios</p>
              ) : (
                savedScenarios.map((s) => (
                  <div key={s.scenario_id} style={studioStyles.savedItem}>
                    <div>
                      <div style={{ fontSize: 13, fontWeight: 500 }}>{s.name}</div>
                      <div style={{ fontSize: 11, color: 'var(--text-muted)' }}>{s.duration_s}s, {s.actors.length} actors</div>
                    </div>
                    <div style={{ display: 'flex', gap: 4 }}>
                      <button onClick={() => loadScenario(s)} style={studioStyles.smallBtn}>Load</button>
                      <button onClick={() => deleteScenario(s.scenario_id)} style={{ ...studioStyles.smallBtn, color: 'var(--accent-red)' }}>
                        <Trash2 size={12} />
                      </button>
                    </div>
                  </div>
                ))
              )}
            </div>
          ) : (
            <div style={studioStyles.form}>
              <div style={studioStyles.field}>
                <label style={studioStyles.label}>Scenario Name</label>
                <input style={studioStyles.input} value={scenario.name} onChange={(e) => update('name', e.target.value)} />
              </div>

              <div style={studioStyles.field}>
                <label style={studioStyles.label}><Map size={12} /> Map Region (bbox)</label>
                <input style={studioStyles.input} placeholder="minLon,minLat,maxLon,maxLat" value={scenario.map_region} onChange={(e) => update('map_region', e.target.value)} />
              </div>

              <div style={studioStyles.row}>
                <div style={studioStyles.field}>
                  <label style={studioStyles.label}>Random Seed</label>
                  <input style={studioStyles.input} type="number" value={scenario.random_seed} onChange={(e) => update('random_seed', parseInt(e.target.value) || 0)} />
                </div>
                <div style={studioStyles.field}>
                  <label style={studioStyles.label}><Clock size={12} /> Duration (s)</label>
                  <input style={studioStyles.input} type="number" value={scenario.duration_s} onChange={(e) => update('duration_s', parseInt(e.target.value) || 60)} />
                </div>
              </div>

              <div style={studioStyles.field}>
                <label style={studioStyles.label}><Users size={12} /> Traffic Demand</label>
                <div style={studioStyles.presetRow}>
                  {DEMAND_PRESETS.map((p) => (
                    <button key={p} onClick={() => update('demand_profile', p)} style={{
                      ...studioStyles.presetBtn,
                      background: scenario.demand_profile === p ? 'var(--accent-blue)' : 'var(--bg-primary)',
                      color: scenario.demand_profile === p ? '#fff' : 'var(--text-secondary)',
                    }}>{p}</button>
                  ))}
                </div>
              </div>

              <div style={studioStyles.field}>
                <label style={studioStyles.label}><Wifi size={12} /> Network Profile</label>
                <div style={studioStyles.presetRow}>
                  {NETWORK_PRESETS.map((p) => (
                    <button key={p} onClick={() => update('network_profile', p)} style={{
                      ...studioStyles.presetBtn,
                      background: scenario.network_profile === p ? 'var(--accent-blue)' : 'var(--bg-primary)',
                      color: scenario.network_profile === p ? '#fff' : 'var(--text-secondary)',
                    }}>{p}</button>
                  ))}
                </div>
              </div>

              <div style={studioStyles.field}>
                <label style={studioStyles.label}><Satellite size={12} /> GPS Profile</label>
                <div style={studioStyles.presetRow}>
                  {GPS_PRESETS.map((p) => (
                    <button key={p} onClick={() => update('gps_profile', p)} style={{
                      ...studioStyles.presetBtn,
                      background: scenario.gps_profile === p ? 'var(--accent-blue)' : 'var(--bg-primary)',
                      color: scenario.gps_profile === p ? '#fff' : 'var(--text-secondary)',
                    }}>{p}</button>
                  ))}
                </div>
              </div>

              <div style={studioStyles.field}>
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                  <label style={studioStyles.label}><Users size={12} /> Actors ({scenario.actors.length})</label>
                  <button onClick={addActor} style={studioStyles.addBtn}><Plus size={12} /> Add</button>
                </div>
                {scenario.actors.map((a, i) => (
                  <div key={a.actor_id} style={studioStyles.listItem}>
                    <span style={{ fontSize: 12, fontFamily: 'var(--font-mono)' }}>{a.actor_type} @ {a.start_position.lat.toFixed(3)},{a.start_position.lon.toFixed(3)}</span>
                    <button onClick={() => removeActor(i)} style={studioStyles.removeBtn}><Trash2 size={12} /></button>
                  </div>
                ))}
              </div>

              <div style={studioStyles.field}>
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                  <label style={studioStyles.label}><Zap size={12} /> Scheduled Events ({scenario.scheduled_events.length})</label>
                  <button onClick={addEvent} style={studioStyles.addBtn}><Plus size={12} /> Add</button>
                </div>
                {scenario.scheduled_events.map((e, i) => (
                  <div key={i} style={studioStyles.listItem}>
                    <span style={{ fontSize: 12, fontFamily: 'var(--font-mono)' }}>@{e.time_s}s: {e.event_type}</span>
                    <button onClick={() => removeEvent(i)} style={studioStyles.removeBtn}><Trash2 size={12} /></button>
                  </div>
                ))}
              </div>

              <button style={studioStyles.runBtn}>
                <Play size={16} /> Run Scenario
              </button>
            </div>
          )}
        </aside>

        <div style={studioStyles.mapArea}>
          <MapView />
        </div>
      </div>
    </div>
  );
}

const studioStyles: Record<string, React.CSSProperties> = {
  container: { display: 'flex', flexDirection: 'column', height: '100vh', overflow: 'hidden' },
  header: {
    display: 'flex', alignItems: 'center', height: 48, padding: '0 16px',
    background: 'var(--bg-secondary)', borderBottom: '1px solid var(--border-primary)', flexShrink: 0,
  },
  backLink: {
    display: 'flex', alignItems: 'center', gap: 6, color: 'var(--text-secondary)',
    textDecoration: 'none', fontSize: 13, fontWeight: 500,
  },
  headerCenter: { display: 'flex', alignItems: 'center', gap: 8, margin: '0 auto' },
  headerBtn: {
    display: 'flex', alignItems: 'center', gap: 6, padding: '6px 14px',
    background: 'var(--bg-tertiary)', border: '1px solid var(--border-primary)',
    borderRadius: 'var(--radius-md)', color: 'var(--text-primary)',
    fontSize: 12, fontWeight: 500, cursor: 'pointer',
  },
  main: { flex: 1, display: 'flex', overflow: 'hidden' },
  sidebar: {
    width: 380, flexShrink: 0, overflow: 'auto', padding: 16,
    background: 'var(--bg-secondary)', borderRight: '1px solid var(--border-primary)',
  },
  mapArea: { flex: 1, position: 'relative' },
  form: { display: 'flex', flexDirection: 'column', gap: 14 },
  field: { display: 'flex', flexDirection: 'column', gap: 6 },
  label: {
    display: 'flex', alignItems: 'center', gap: 6, fontSize: 11, fontWeight: 600,
    color: 'var(--text-secondary)', textTransform: 'uppercase', letterSpacing: 0.5,
  },
  input: {
    padding: '8px 12px', background: 'var(--bg-primary)', border: '1px solid var(--border-primary)',
    borderRadius: 'var(--radius-md)', color: 'var(--text-primary)', fontSize: 13,
    fontFamily: 'var(--font-mono)',
  },
  row: { display: 'flex', gap: 12 },
  presetRow: { display: 'flex', gap: 4 },
  presetBtn: {
    flex: 1, padding: '6px 8px', border: 'none', borderRadius: 'var(--radius-sm)',
    fontSize: 11, fontWeight: 500, cursor: 'pointer', textTransform: 'capitalize',
  },
  addBtn: {
    display: 'flex', alignItems: 'center', gap: 4, padding: '4px 10px',
    background: 'var(--bg-tertiary)', border: '1px solid var(--border-primary)',
    borderRadius: 'var(--radius-sm)', color: 'var(--accent-blue)',
    fontSize: 11, cursor: 'pointer',
  },
  listItem: {
    display: 'flex', alignItems: 'center', justifyContent: 'space-between',
    padding: '6px 8px', background: 'var(--bg-primary)', borderRadius: 'var(--radius-sm)',
    marginTop: 4,
  },
  removeBtn: {
    background: 'none', border: 'none', color: 'var(--accent-red)', cursor: 'pointer', padding: 2,
  },
  runBtn: {
    display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8,
    padding: '12px 20px', background: 'var(--accent-green)', border: 'none',
    borderRadius: 'var(--radius-md)', color: '#fff', fontSize: 14,
    fontWeight: 600, cursor: 'pointer', marginTop: 8,
  },
  sectionTitle: {
    display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, fontWeight: 600,
    color: 'var(--text-primary)', marginBottom: 12,
  },
  savedItem: {
    display: 'flex', alignItems: 'center', justifyContent: 'space-between',
    padding: 12, background: 'var(--bg-elevated)', borderRadius: 'var(--radius-md)',
    marginBottom: 8,
  },
  smallBtn: {
    padding: '4px 10px', background: 'var(--bg-tertiary)', border: '1px solid var(--border-primary)',
    borderRadius: 'var(--radius-sm)', color: 'var(--text-primary)',
    fontSize: 11, cursor: 'pointer',
  },
};
