import { useState, useEffect } from 'react';
import { Link } from 'react-router-dom';
import { MapView } from '../map/MapView';
import { AlertPanel } from './AlertPanel';
import { Inspector } from './Inspector';
import { SystemHealth } from './SystemHealth';
import { LayerControls } from './LayerControls';
import { useUIStore } from '../state/uiStore';
import { useWorldStore } from '../state/worldStore';
import { useAlertStore } from '../state/alertStore';
import {
  Activity,
  AlertTriangle,
  Car,
  Layers,
  Monitor,
  Radio,
  Search,
  Settings,
  Wifi,
  WifiOff,
} from 'lucide-react';

type SidebarTab = 'alerts' | 'inspector' | 'health' | 'layers';

export function Dashboard() {
  const [activeTab, setActiveTab] = useState<SidebarTab>('alerts');
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const selectEntity = useUIStore((s) => s.selectEntity);
  const selectedEntityId = useUIStore((s) => s.selectedEntityId);
  const connectivity = useWorldStore((s) => s.connectivity);
  const metrics = useWorldStore((s) => s.metrics);
  const vehicles = useWorldStore((s) => s.vehicles);
  const hazards = useWorldStore((s) => s.hazards);
  const alerts = useAlertStore((s) => s.sorted);
  const lastUpdate = useWorldStore((s) => s.lastUpdate);

  useEffect(() => {
    if (selectedEntityId) setActiveTab('inspector');
  }, [selectedEntityId]);

  const handleEntityClick = (id: string, type: string) => {
    selectEntity(id, type);
    setActiveTab('inspector');
  };

  const criticalCount = alerts.filter((a) => a.severity === 'CRITICAL' || a.severity === 'HIGH').length;

  return (
    <div style={styles.container}>
      <header style={styles.header}>
        <div style={styles.headerLeft}>
          <div style={styles.logo}>
            <Radio size={20} style={{ color: 'var(--accent-blue)' }} />
            <span style={styles.logoText}>MARGA</span>
            <span style={styles.logoSub}>V2X Control Center</span>
          </div>
        </div>
        <nav style={styles.nav}>
          <Link to="/" style={{ ...styles.navLink, ...styles.navLinkActive }}>
            <Monitor size={16} /> Control Center
          </Link>
          <Link to="/driver" style={styles.navLink}>
            <Car size={16} /> Driver Console
          </Link>
          <Link to="/scenarios" style={styles.navLink}>
            <Settings size={16} /> Scenarios
          </Link>
          <Link to="/replay" style={styles.navLink}>
            <Search size={16} /> Replay
          </Link>
        </nav>
        <div style={styles.headerRight}>
          <div style={{
            ...styles.statusBadge,
            background: connectivity === 'FULL' ? 'rgba(34,197,94,0.15)' : 'rgba(234,179,8,0.15)',
            color: connectivity === 'FULL' ? 'var(--accent-green)' : 'var(--accent-yellow)',
          }}>
            {connectivity === 'FULL' ? <Wifi size={14} /> : <WifiOff size={14} />}
            {connectivity}
          </div>
          <div style={{ ...styles.statusBadge, background: 'rgba(74,125,255,0.15)', color: 'var(--accent-blue)' }}>
            <Activity size={14} /> SIM
          </div>
        </div>
      </header>

      <div style={styles.main}>
        <div style={styles.mapArea}>
          <MapView onEntityClick={handleEntityClick} />
          {criticalCount > 0 && (
            <div style={styles.criticalBanner}>
              <AlertTriangle size={16} />
              {criticalCount} critical alert{criticalCount > 1 ? 's' : ''} active
            </div>
          )}
        </div>

        {sidebarOpen && (
          <aside style={styles.sidebar}>
            <div style={styles.tabs}>
              {([
                { id: 'alerts' as const, icon: <AlertTriangle size={16} />, label: 'Alerts', count: alerts.length },
                { id: 'inspector' as const, icon: <Search size={16} />, label: 'Inspector' },
                { id: 'health' as const, icon: <Activity size={16} />, label: 'Health' },
                { id: 'layers' as const, icon: <Layers size={16} />, label: 'Layers' },
              ]).map((tab) => (
                <button
                  key={tab.id}
                  onClick={() => setActiveTab(tab.id)}
                  style={{
                    ...styles.tab,
                    ...(activeTab === tab.id ? styles.tabActive : {}),
                  }}
                >
                  {tab.icon}
                  <span>{tab.label}</span>
                  {tab.count !== undefined && tab.count > 0 && (
                    <span style={styles.tabBadge}>{tab.count}</span>
                  )}
                </button>
              ))}
            </div>
            <div style={styles.tabContent}>
              {activeTab === 'alerts' && <AlertPanel />}
              {activeTab === 'inspector' && <Inspector />}
              {activeTab === 'health' && <SystemHealth />}
              {activeTab === 'layers' && <LayerControls />}
            </div>
          </aside>
        )}

        <button
          onClick={() => setSidebarOpen(!sidebarOpen)}
          style={styles.sidebarToggle}
          title={sidebarOpen ? 'Hide sidebar' : 'Show sidebar'}
        >
          {sidebarOpen ? '›' : '‹'}
        </button>
      </div>

      <footer style={styles.footer}>
        <span>Actors: {vehicles.size + useWorldStore.getState().pedestrians.size}</span>
        <span>Hazards: {hazards.size}</span>
        <span>Events/s: {metrics?.actor_updates_per_sec ?? '—'}</span>
        <span>Risk evals/s: {metrics?.risk_evaluations_per_sec ?? '—'}</span>
        <span>Bus lag: {metrics?.event_bus_lag_ms ?? '—'}ms</span>
        <span style={{ marginLeft: 'auto', color: 'var(--text-muted)' }}>
          Last: {lastUpdate ? new Date(lastUpdate).toLocaleTimeString() : '—'}
        </span>
      </footer>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  container: {
    display: 'flex',
    flexDirection: 'column',
    height: '100vh',
    width: '100vw',
    overflow: 'hidden',
    background: 'var(--bg-primary)',
  },
  header: {
    display: 'flex',
    alignItems: 'center',
    height: 48,
    padding: '0 16px',
    background: 'var(--bg-secondary)',
    borderBottom: '1px solid var(--border-primary)',
    gap: 16,
    flexShrink: 0,
  },
  headerLeft: { display: 'flex', alignItems: 'center', gap: 12 },
  logo: { display: 'flex', alignItems: 'center', gap: 8 },
  logoText: { fontWeight: 700, fontSize: 16, letterSpacing: 2, color: 'var(--text-primary)' },
  logoSub: { fontSize: 11, color: 'var(--text-muted)', fontWeight: 400 },
  nav: { display: 'flex', gap: 4, marginLeft: 24 },
  navLink: {
    display: 'flex', alignItems: 'center', gap: 6, padding: '6px 12px',
    borderRadius: 'var(--radius-md)', fontSize: 13, fontWeight: 500,
    color: 'var(--text-secondary)', textDecoration: 'none',
    transition: 'var(--transition-fast)',
  },
  navLinkActive: {
    color: 'var(--text-primary)', background: 'var(--bg-tertiary)',
  },
  headerRight: { marginLeft: 'auto', display: 'flex', gap: 8 },
  statusBadge: {
    display: 'flex', alignItems: 'center', gap: 6, padding: '4px 10px',
    borderRadius: 'var(--radius-sm)', fontSize: 11, fontWeight: 600, letterSpacing: 0.5,
  },
  main: { display: 'flex', flex: 1, overflow: 'hidden', position: 'relative' },
  mapArea: { flex: 1, position: 'relative', overflow: 'hidden' },
  criticalBanner: {
    position: 'absolute', top: 12, left: '50%', transform: 'translateX(-50%)',
    display: 'flex', alignItems: 'center', gap: 8, padding: '8px 20px',
    background: 'rgba(239,68,68,0.9)', color: '#fff', borderRadius: 'var(--radius-md)',
    fontSize: 13, fontWeight: 600, zIndex: 10, boxShadow: 'var(--shadow-lg)',
    animation: 'pulse 2s ease-in-out infinite',
  },
  sidebar: {
    width: 360, flexShrink: 0, display: 'flex', flexDirection: 'column',
    background: 'var(--bg-secondary)', borderLeft: '1px solid var(--border-primary)',
    overflow: 'hidden',
  },
  sidebarToggle: {
    position: 'absolute', right: 360, top: '50%', transform: 'translateY(-50%)',
    width: 20, height: 48, background: 'var(--bg-tertiary)',
    border: '1px solid var(--border-primary)', borderRight: 'none',
    borderRadius: '4px 0 0 4px', color: 'var(--text-muted)',
    cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center',
    fontSize: 16, zIndex: 5,
  },
  tabs: {
    display: 'flex', borderBottom: '1px solid var(--border-primary)', flexShrink: 0,
  },
  tab: {
    flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6,
    padding: '10px 8px', background: 'none', border: 'none', borderBottom: '2px solid transparent',
    color: 'var(--text-muted)', fontSize: 12, fontWeight: 500, cursor: 'pointer',
    transition: 'var(--transition-fast)',
  },
  tabActive: {
    color: 'var(--accent-blue)', borderBottomColor: 'var(--accent-blue)',
  },
  tabBadge: {
    padding: '1px 6px', borderRadius: 10, background: 'var(--accent-red)',
    color: '#fff', fontSize: 10, fontWeight: 700,
  },
  tabContent: { flex: 1, overflow: 'auto', padding: 12 },
  footer: {
    display: 'flex', alignItems: 'center', gap: 16, padding: '0 16px',
    height: 28, background: 'var(--bg-secondary)', borderTop: '1px solid var(--border-primary)',
    fontSize: 11, color: 'var(--text-secondary)', flexShrink: 0,
  },
};
