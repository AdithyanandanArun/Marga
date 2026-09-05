import { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { Activity, AlertTriangle, Crosshair, Layers, Radio, Search, Settings, ShieldCheck, TrainTrack, Wifi, WifiOff } from 'lucide-react';
import { MapView } from '../map/MapView';
import { AlertPanel } from './AlertPanel';
import { Inspector } from './Inspector';
import { SystemHealth } from './SystemHealth';
import { LayerControls } from './LayerControls';
import { WorldStream } from '../net/worldStream';
import { useUIStore } from '../state/uiStore';
import { useWorldStore } from '../state/worldStore';
import type { RiskEvent, VehicleState } from '../types/canonical';
import { selectPrimaryRisk } from '../utils/risk';

type AdvancedTab = 'alerts' | 'inspector' | 'health' | 'layers';
type Tone = 'good' | 'warning' | 'muted';
const TONE: Record<Tone, string> = { good: 'var(--accent-green)', warning: 'var(--accent-yellow)', muted: 'var(--text-secondary)' };

function actorName(actor: VehicleState | undefined, fallback: string): string {
  return actor ? `${actor.actor_type.toLowerCase()} ${actor.actor_id.replace(/^.*[-_:]/, '')}` : fallback;
}
function riskHeadline(risk: RiskEvent | undefined, vehicles: Map<string, VehicleState>): string {
  if (!risk) return 'No active conflict';
  const [a, b] = risk.affected_actor_ids;
  return `${actorName(vehicles.get(a), a)} and ${actorName(vehicles.get(b), b)} are approaching the same path`;
}

export function Dashboard() {
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [advancedTab, setAdvancedTab] = useState<AdvancedTab>('alerts');
  const [gatewayConnected, setGatewayConnected] = useState(false);
  const selectEntity = useUIStore((s) => s.selectEntity);
  const vehicles = useWorldStore((s) => s.vehicles);
  const pedestrians = useWorldStore((s) => s.pedestrians);
  const risks = useWorldStore((s) => s.risks);
  const connectivity = useWorldStore((s) => s.connectivity);
  const lastUpdate = useWorldStore((s) => s.lastUpdate);

  useEffect(() => {
    const stream = new WorldStream({ onConnectionChange: setGatewayConnected });
    stream.connect();
    return () => stream.disconnect();
  }, []);

  const primaryRisk = useMemo(() => selectPrimaryRisk(risks.values()), [risks]);
  const riskActors = primaryRisk?.affected_actor_ids.map((id) => vehicles.get(id)).filter(Boolean) as VehicleState[] | undefined;
  const gpsUncertainty = riskActors?.length ? Math.max(...riskActors.map((actor) => actor.position_uncertainty_m)) : undefined;
  const directV2x = connectivity === 'DIRECT_ONLY' || connectivity === 'FULL';

  return <main style={s.shell} aria-label="Marga V2X resilience dashboard">
    <header style={s.header}>
      <div style={s.brand}><Radio size={19} aria-hidden="true" style={{ color: 'var(--accent-cyan)' }} /><strong style={s.brandName}>MARGA</strong><span style={s.brandLine}>INDIA-READY V2X</span></div>
      <div style={s.headerStatus} aria-live="polite">
        <Pill icon={gatewayConnected ? <Activity size={14} /> : <WifiOff size={14} />} label={gatewayConnected ? 'Gateway live' : 'Gateway reconnecting'} tone={gatewayConnected ? 'good' : 'muted'} />
        <Pill icon={directV2x ? <Radio size={14} /> : <WifiOff size={14} />} label={directV2x ? 'Direct V2X ready' : 'Direct V2X unavailable'} tone={directV2x ? 'good' : 'warning'} />
        <Link to="/simulation" style={{ ...s.advancedButton, textDecoration: 'none' }}><TrainTrack size={15} /> Junction Simulator</Link>
        <button onClick={() => setAdvancedOpen((value) => !value)} style={s.advancedButton} aria-expanded={advancedOpen}><Settings size={15} /> Advanced</button>
      </div>
    </header>
    <section style={s.content}>
      <div style={s.mapArea}>
        <MapView roadScene onEntityClick={(id, type) => { selectEntity(id, type); setAdvancedTab('inspector'); setAdvancedOpen(true); }} />
        <section style={s.resilienceRail} aria-label="Resilience status">
          <Status icon={<Wifi size={17} />} label="Internet" value={connectivity === 'DIRECT_ONLY' ? 'Offline' : gatewayConnected ? 'Online' : 'Reconnecting'} detail={connectivity === 'DIRECT_ONLY' ? 'Safety stays local' : 'Cloud path available'} tone={connectivity === 'DIRECT_ONLY' ? 'warning' : gatewayConnected ? 'good' : 'muted'} />
          <Status icon={<Crosshair size={17} />} label="Positioning" value={gpsUncertainty ? `GPS ±${Math.round(gpsUncertainty)} m` : 'Awaiting GPS'} detail={gpsUncertainty ? 'Confidence adjusts to uncertainty' : 'No active road user'} tone={gpsUncertainty && gpsUncertainty > 15 ? 'warning' : 'good'} />
          <Status icon={<Radio size={17} />} label="Safety link" value={directV2x ? 'Direct V2X' : 'Link unavailable'} detail={directV2x ? 'Nearby warning path active' : 'No local peer path'} tone={directV2x ? 'good' : 'muted'} />
        </section>
        <section style={{ ...s.incidentCard, ...(primaryRisk ? s.incidentActive : {}) }} aria-live="polite">
          <div style={s.eyebrow}>{primaryRisk ? <><AlertTriangle size={15} /> COLLISION RISK</> : <><ShieldCheck size={15} /> ROAD SAFETY MONITOR</>}</div>
          <h1 style={s.incidentTitle}>{riskHeadline(primaryRisk, vehicles)}</h1>
          {primaryRisk ? <>
            <div style={s.metricRow}><Metric label="Time to conflict" value={`${primaryRisk.time_to_conflict_s.toFixed(1)} s`} /><Metric label="Confidence" value={`${Math.round(primaryRisk.confidence * 100)}%`} /></div>
            <p style={s.incidentExplanation}>Predicted paths overlap at the junction. Reduce speed before the conflict point; the warning stays available over the local safety link.</p>
          </> : <p style={s.incidentExplanation}>Marga is receiving only verified road telemetry. When a conflict is predicted, this panel will show one clear reason and the safety action.</p>}
        </section>
        <div style={s.mapCaption}><span><i style={s.legendDot} />Road users</span><span><i style={{ ...s.legendDot, background: 'var(--accent-red)' }} />Predicted conflict</span><span>{vehicles.size + pedestrians.size} tracked</span></div>
      </div>
      {advancedOpen && <aside style={s.advancedPanel} aria-label="Advanced system detail">
        <div style={s.advancedHeading}><div><span style={s.advancedKicker}>SUPPORTING DETAIL</span><strong>Advanced monitor</strong></div><button onClick={() => setAdvancedOpen(false)} style={s.closeButton} aria-label="Close advanced monitor">×</button></div>
        <nav style={s.tabs} aria-label="Advanced views">
          {([['alerts', <AlertTriangle size={14} />, 'Alerts'], ['inspector', <Search size={14} />, 'Evidence'], ['health', <Activity size={14} />, 'Health'], ['layers', <Layers size={14} />, 'Layers']] as const).map(([id, icon, label]) => <button key={id} onClick={() => setAdvancedTab(id)} style={{ ...s.tab, ...(advancedTab === id ? s.tabActive : {}) }}>{icon}{label}</button>)}
        </nav>
        <div style={s.advancedContent}>{advancedTab === 'alerts' && <AlertPanel />}{advancedTab === 'inspector' && <Inspector />}{advancedTab === 'health' && <SystemHealth />}{advancedTab === 'layers' && <LayerControls />}</div>
      </aside>}
    </section>
    <footer style={s.footer}><span>Mixed traffic: {vehicles.size} vehicles · {pedestrians.size} pedestrians</span><span>Junction view · ±300 m</span><span style={{ marginLeft: 'auto' }}>Last verified update: {lastUpdate ? new Date(lastUpdate).toLocaleTimeString() : '—'}</span></footer>
  </main>;
}

function Pill({ icon, label, tone }: { icon: React.ReactNode; label: string; tone: Tone }) { return <span style={{ ...s.statusPill, color: TONE[tone] }}>{icon}{label}</span>; }
function Status({ icon, label, value, detail, tone }: { icon: React.ReactNode; label: string; value: string; detail: string; tone: Tone }) { return <div style={s.statusCard}><span style={{ ...s.cardIcon, color: TONE[tone] }}>{icon}</span><div><div style={s.statusLabel}>{label}</div><strong style={s.statusValue}>{value}</strong><div style={s.statusDetail}>{detail}</div></div></div>; }
function Metric({ label, value }: { label: string; value: string }) { return <div><div style={s.metricLabel}>{label}</div><strong style={s.metricValue}>{value}</strong></div>; }

const s: Record<string, React.CSSProperties> = {
  shell: { height: '100vh', width: '100vw', display: 'flex', flexDirection: 'column', overflow: 'hidden', background: 'var(--bg-primary)' },
  header: { minHeight: 58, padding: '0 20px', display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 16, background: 'rgba(15,17,23,0.98)', borderBottom: '1px solid var(--border-primary)' },
  brand: { display: 'flex', alignItems: 'center', gap: 9, minWidth: 0 }, brandName: { letterSpacing: 2.4, fontSize: 16 }, brandLine: { fontSize: 11, letterSpacing: 1.1, color: 'var(--text-muted)', borderLeft: '1px solid var(--border-primary)', paddingLeft: 10 },
  headerStatus: { display: 'flex', alignItems: 'center', gap: 8 }, statusPill: { display: 'inline-flex', alignItems: 'center', gap: 6, padding: '6px 9px', border: '1px solid var(--border-primary)', borderRadius: 6, background: 'var(--bg-secondary)', fontSize: 11, fontWeight: 650 }, advancedButton: { minHeight: 36, display: 'inline-flex', alignItems: 'center', gap: 7, padding: '0 11px', border: '1px solid var(--border-primary)', borderRadius: 6, background: 'transparent', color: 'var(--text-secondary)', cursor: 'pointer', fontSize: 12, fontWeight: 600 },
  content: { flex: 1, minHeight: 0, display: 'flex' }, mapArea: { flex: 1, minWidth: 0, position: 'relative', overflow: 'hidden' }, resilienceRail: { position: 'absolute', left: 16, top: 16, display: 'grid', gap: 8, zIndex: 3, width: 222 },
  statusCard: { display: 'flex', alignItems: 'flex-start', gap: 10, padding: 12, border: '1px solid rgba(255,255,255,0.12)', borderRadius: 9, background: 'rgba(15,17,23,0.91)', boxShadow: 'var(--shadow-md)' }, cardIcon: { marginTop: 2 }, statusLabel: { color: 'var(--text-muted)', fontSize: 10, fontWeight: 700, letterSpacing: 0.8, textTransform: 'uppercase' }, statusValue: { display: 'block', marginTop: 3, fontSize: 13, color: 'var(--text-primary)' }, statusDetail: { marginTop: 2, fontSize: 11, color: 'var(--text-secondary)', lineHeight: 1.35 },
  incidentCard: { position: 'absolute', right: 16, bottom: 24, zIndex: 3, width: 360, padding: 18, borderRadius: 12, background: 'rgba(15,17,23,0.94)', border: '1px solid rgba(255,255,255,0.14)', boxShadow: 'var(--shadow-lg)' }, incidentActive: { borderColor: 'rgba(248,113,113,0.72)', boxShadow: '0 8px 28px rgba(127,29,29,0.35)' }, eyebrow: { display: 'flex', alignItems: 'center', gap: 6, color: 'var(--accent-red)', fontSize: 11, fontWeight: 800, letterSpacing: 1 }, incidentTitle: { margin: '9px 0 12px', fontSize: 18, lineHeight: 1.25, textTransform: 'capitalize' }, metricRow: { display: 'flex', gap: 28, padding: '11px 0', borderTop: '1px solid var(--border-primary)', borderBottom: '1px solid var(--border-primary)' }, metricLabel: { color: 'var(--text-muted)', fontSize: 10, textTransform: 'uppercase', letterSpacing: 0.6 }, metricValue: { display: 'block', marginTop: 3, fontSize: 17, fontVariantNumeric: 'tabular-nums' }, incidentExplanation: { marginTop: 12, color: 'var(--text-secondary)', fontSize: 12, lineHeight: 1.5 },
  mapCaption: { position: 'absolute', left: 16, bottom: 15, zIndex: 3, display: 'flex', gap: 13, padding: '6px 9px', background: 'rgba(15,17,23,0.82)', borderRadius: 5, color: 'var(--text-secondary)', fontSize: 10 }, legendDot: { display: 'inline-block', width: 6, height: 6, marginRight: 5, borderRadius: '50%', background: 'var(--accent-blue)' },
  advancedPanel: { width: 360, flexShrink: 0, display: 'flex', flexDirection: 'column', background: 'var(--bg-secondary)', borderLeft: '1px solid var(--border-primary)' }, advancedHeading: { display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', padding: 16, borderBottom: '1px solid var(--border-primary)', fontSize: 14 }, advancedKicker: { display: 'block', marginBottom: 4, color: 'var(--text-muted)', fontSize: 10, letterSpacing: 0.9, fontWeight: 700 }, closeButton: { width: 32, height: 32, border: 'none', background: 'transparent', color: 'var(--text-secondary)', cursor: 'pointer', fontSize: 22 }, tabs: { display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 4, padding: 8, borderBottom: '1px solid var(--border-primary)' }, tab: { minHeight: 36, display: 'flex', justifyContent: 'center', alignItems: 'center', gap: 6, border: '1px solid transparent', borderRadius: 5, background: 'transparent', color: 'var(--text-muted)', cursor: 'pointer', fontSize: 11, fontWeight: 600 }, tabActive: { background: 'var(--bg-tertiary)', borderColor: 'var(--border-primary)', color: 'var(--text-primary)' }, advancedContent: { flex: 1, overflow: 'auto', padding: 12 },
  footer: { minHeight: 30, display: 'flex', alignItems: 'center', gap: 14, padding: '0 16px', background: 'var(--bg-secondary)', borderTop: '1px solid var(--border-primary)', color: 'var(--text-muted)', fontSize: 10 },
};
