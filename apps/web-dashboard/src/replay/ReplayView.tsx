import { useState, useCallback } from 'react';
import { Link, useParams } from 'react-router-dom';
import { MapView } from '../map/MapView';
import type { DecisionTrace } from '../types/canonical';
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
} from 'lucide-react';

const MOCK_INCIDENTS = [
  { id: 'inc-001', title: 'Rear-end collision risk — NH44 Junction', time: '14:23:41', duration: 12 },
  { id: 'inc-002', title: 'Animal crossing — Ring Road Sec 3', time: '14:31:15', duration: 8 },
  { id: 'inc-003', title: 'Wrong-way detection — MG Road', time: '15:02:08', duration: 5 },
];

const MOCK_TRACE: DecisionTrace = {
  decision_id: 'dec-001',
  ts: new Date().toISOString(),
  decision_type: 'collision_risk',
  inputs: [
    { entity_id: 'veh-001', version: 42, timestamp: new Date().toISOString() },
    { entity_id: 'veh-012', version: 38, timestamp: new Date().toISOString() },
  ],
  derived_metrics: {
    ttc_s: 2.4,
    relative_speed_mps: 13.3,
    min_distance_m: 4.7,
    confidence: 0.87,
    braking_feasibility: 0.62,
  },
  rules_fired: [
    'intersection_conflict_check',
    'eta_overlap_detection',
    'braking_distance_evaluation',
    'severity_classification',
  ],
  output_ids: ['risk-047', 'alert-012'],
  trace_id: 'trace-abc123',
};

const SPEEDS = [0.25, 0.5, 1, 2, 4];

export function ReplayView() {
  const { incidentId } = useParams();
  const [selectedIncident, setSelectedIncident] = useState(incidentId ?? MOCK_INCIDENTS[0].id);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(1);
  const [currentTime, setCurrentTime] = useState(0);
  const [expandedSections, setExpandedSections] = useState<Set<string>>(new Set(['inputs', 'metrics', 'rules']));

  const incident = MOCK_INCIDENTS.find((i) => i.id === selectedIncident) ?? MOCK_INCIDENTS[0];

  const toggleSection = (section: string) => {
    setExpandedSections((prev) => {
      const next = new Set(prev);
      if (next.has(section)) next.delete(section);
      else next.add(section);
      return next;
    });
  };

  const handleTimeChange = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    setCurrentTime(parseFloat(e.target.value));
  }, []);

  return (
    <div style={replayStyles.container}>
      <header style={replayStyles.header}>
        <Link to="/" style={replayStyles.backLink}><ArrowLeft size={18} /> Control Center</Link>
        <div style={replayStyles.headerCenter}>
          <Clock size={18} style={{ color: 'var(--accent-cyan)' }} />
          <span style={{ fontWeight: 700, fontSize: 15 }}>Incident Replay</span>
        </div>
        <div style={replayStyles.incidentSelect}>
          <select
            value={selectedIncident}
            onChange={(e) => { setSelectedIncident(e.target.value); setCurrentTime(0); }}
            style={replayStyles.select}
          >
            {MOCK_INCIDENTS.map((inc) => (
              <option key={inc.id} value={inc.id}>{inc.title} ({inc.time})</option>
            ))}
          </select>
          <ChevronDown size={14} style={{ position: 'absolute', right: 8, top: '50%', transform: 'translateY(-50%)', color: 'var(--text-muted)', pointerEvents: 'none' }} />
        </div>
      </header>

      <div style={replayStyles.main}>
        <div style={replayStyles.mapSection}>
          <MapView />
        </div>

        <aside style={replayStyles.evidenceSidebar}>
          <h3 style={replayStyles.sidebarTitle}><FileText size={14} /> Decision Trace</h3>

          <Section
            title="Input Entities"
            icon={<Target size={14} />}
            expanded={expandedSections.has('inputs')}
            onToggle={() => toggleSection('inputs')}
          >
            {MOCK_TRACE.inputs.map((inp) => (
              <div key={inp.entity_id} style={replayStyles.traceItem}>
                <span style={replayStyles.traceId}>{inp.entity_id}</span>
                <span style={replayStyles.traceDetail}>v{inp.version}</span>
              </div>
            ))}
          </Section>

          <Section
            title="Derived Metrics"
            icon={<Zap size={14} />}
            expanded={expandedSections.has('metrics')}
            onToggle={() => toggleSection('metrics')}
          >
            {Object.entries(MOCK_TRACE.derived_metrics).map(([key, val]) => (
              <div key={key} style={replayStyles.metricRow}>
                <span style={replayStyles.metricLabel}>{key.replace(/_/g, ' ')}</span>
                <span style={replayStyles.metricValue}>{typeof val === 'number' ? val.toFixed(2) : val}</span>
              </div>
            ))}
          </Section>

          <Section
            title="Rules Fired"
            icon={<AlertTriangle size={14} />}
            expanded={expandedSections.has('rules')}
            onToggle={() => toggleSection('rules')}
          >
            {MOCK_TRACE.rules_fired.map((rule, i) => (
              <div key={i} style={replayStyles.ruleItem}>
                <span style={replayStyles.ruleIndex}>{i + 1}</span>
                <span style={replayStyles.ruleName}>{rule.replace(/_/g, ' ')}</span>
              </div>
            ))}
          </Section>

          <Section
            title="Outputs"
            icon={<FileText size={14} />}
            expanded={expandedSections.has('outputs')}
            onToggle={() => toggleSection('outputs')}
          >
            {MOCK_TRACE.output_ids.map((id) => (
              <div key={id} style={replayStyles.traceItem}>
                <span style={replayStyles.traceId}>{id}</span>
              </div>
            ))}
          </Section>

          <div style={replayStyles.traceFooter}>
            <span style={{ fontSize: 10, color: 'var(--text-muted)' }}>Trace: {MOCK_TRACE.trace_id}</span>
          </div>
        </aside>
      </div>

      <div style={replayStyles.controls}>
        <div style={replayStyles.controlsLeft}>
          <button onClick={() => setCurrentTime(0)} style={replayStyles.controlBtn}><SkipBack size={16} /></button>
          <button onClick={() => setCurrentTime(Math.max(0, currentTime - 1))} style={replayStyles.controlBtn}><Rewind size={16} /></button>
          <button onClick={() => setPlaying(!playing)} style={{ ...replayStyles.controlBtn, ...replayStyles.playBtn }}>
            {playing ? <Pause size={18} /> : <Play size={18} />}
          </button>
          <button onClick={() => setCurrentTime(Math.min(incident.duration, currentTime + 1))} style={replayStyles.controlBtn}><FastForward size={16} /></button>
          <button onClick={() => setCurrentTime(incident.duration)} style={replayStyles.controlBtn}><SkipForward size={16} /></button>
        </div>

        <div style={replayStyles.timeline}>
          <span style={replayStyles.timeLabel}>{currentTime.toFixed(1)}s</span>
          <input
            type="range"
            min={0}
            max={incident.duration}
            step={0.1}
            value={currentTime}
            onChange={handleTimeChange}
            style={replayStyles.slider}
          />
          <span style={replayStyles.timeLabel}>{incident.duration}s</span>
        </div>

        <div style={replayStyles.speedControl}>
          {SPEEDS.map((s) => (
            <button
              key={s}
              onClick={() => setSpeed(s)}
              style={{
                ...replayStyles.speedBtn,
                background: speed === s ? 'var(--accent-blue)' : 'var(--bg-primary)',
                color: speed === s ? '#fff' : 'var(--text-secondary)',
              }}
            >
              {s}x
            </button>
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
  incidentSelect: { position: 'relative', marginLeft: 'auto' },
  select: {
    padding: '6px 28px 6px 10px', appearance: 'none',
    background: 'var(--bg-tertiary)', border: '1px solid var(--border-primary)',
    borderRadius: 'var(--radius-md)', color: 'var(--text-primary)', fontSize: 12,
  },
  main: { flex: 1, display: 'flex', overflow: 'hidden' },
  mapSection: { flex: 1, position: 'relative' },
  evidenceSidebar: {
    width: 340, flexShrink: 0, overflow: 'auto', padding: 12,
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
  ruleItem: { display: 'flex', alignItems: 'center', gap: 8, padding: '4px 0' },
  ruleIndex: {
    width: 18, height: 18, borderRadius: '50%', background: 'var(--bg-primary)',
    display: 'flex', alignItems: 'center', justifyContent: 'center',
    fontSize: 10, color: 'var(--text-muted)', fontWeight: 600, flexShrink: 0,
  },
  ruleName: { fontSize: 12, color: 'var(--text-primary)', textTransform: 'capitalize' },
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
  timeLabel: { fontSize: 12, color: 'var(--text-muted)', fontFamily: 'var(--font-mono)', width: 40 },
  slider: { flex: 1, accentColor: 'var(--accent-blue)' },
  speedControl: { display: 'flex', gap: 2 },
  speedBtn: {
    padding: '4px 8px', border: 'none', borderRadius: 'var(--radius-sm)',
    fontSize: 11, fontWeight: 600, cursor: 'pointer',
  },
};
