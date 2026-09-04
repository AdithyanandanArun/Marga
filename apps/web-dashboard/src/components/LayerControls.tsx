import { useUIStore } from '../state/uiStore';
import {
  Circle,
  GitBranch,
  AlertTriangle,
  Radio,
  Eye,
  Construction,
  Signpost,
  Wifi,
} from 'lucide-react';

interface LayerToggle {
  id: 'uncertainty' | 'trajectories' | 'riskZones' | 'v2xLinks' | 'signals' | 'hazards' | 'roadEvents' | 'rsus';
  label: string;
  icon: React.ReactNode;
  description: string;
  storeKey: 'showUncertainty' | 'showTrajectories' | 'showRiskZones' | 'showV2XLinks' | 'showSignals' | 'showHazards' | 'showRoadEvents' | 'showRSUs';
}

const LAYERS: LayerToggle[] = [
  { id: 'uncertainty', label: 'Uncertainty Rings', icon: <Circle size={14} />, description: 'Position confidence circles', storeKey: 'showUncertainty' },
  { id: 'trajectories', label: 'Trajectories', icon: <GitBranch size={14} />, description: 'Predicted actor paths', storeKey: 'showTrajectories' },
  { id: 'riskZones', label: 'Risk Zones', icon: <AlertTriangle size={14} />, description: 'Conflict detection overlays', storeKey: 'showRiskZones' },
  { id: 'v2xLinks', label: 'V2X Links', icon: <Wifi size={14} />, description: 'Communication connections', storeKey: 'showV2XLinks' },
  { id: 'signals', label: 'Traffic Signals', icon: <Signpost size={14} />, description: 'Signal phase indicators', storeKey: 'showSignals' },
  { id: 'hazards', label: 'Hazards', icon: <AlertTriangle size={14} />, description: 'Road hazards and obstacles', storeKey: 'showHazards' },
  { id: 'roadEvents', label: 'Road Events', icon: <Construction size={14} />, description: 'Closures, narrowing, works', storeKey: 'showRoadEvents' },
  { id: 'rsus', label: 'RSUs', icon: <Radio size={14} />, description: 'Roadside unit coverage', storeKey: 'showRSUs' },
];

export function LayerControls() {
  const toggleLayer = useUIStore((s) => s.toggleLayer);
  const store = useUIStore();

  return (
    <div>
      <div style={styles.header}>
        <Eye size={14} style={{ color: 'var(--accent-blue)' }} />
        <span style={styles.headerText}>Map Layers</span>
      </div>

      <div style={styles.list}>
        {LAYERS.map((layer) => {
          const isOn = store[layer.storeKey] as boolean;
          return (
            <button
              key={layer.id}
              onClick={() => toggleLayer(layer.id)}
              style={{
                ...styles.layerItem,
                opacity: isOn ? 1 : 0.5,
              }}
            >
              <div style={{
                ...styles.toggle,
                background: isOn ? 'var(--accent-blue)' : 'var(--bg-primary)',
                borderColor: isOn ? 'var(--accent-blue)' : 'var(--border-primary)',
              }}>
                <div style={{
                  ...styles.toggleDot,
                  transform: isOn ? 'translateX(14px)' : 'translateX(0)',
                  background: isOn ? '#fff' : 'var(--text-muted)',
                }} />
              </div>
              <div>
                <div style={styles.layerLabel}>
                  {layer.icon}
                  {layer.label}
                </div>
                <div style={styles.layerDesc}>{layer.description}</div>
              </div>
            </button>
          );
        })}
      </div>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  header: {
    display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12,
  },
  headerText: {
    fontSize: 12, fontWeight: 600, color: 'var(--text-secondary)',
    textTransform: 'uppercase', letterSpacing: 0.5,
  },
  list: { display: 'flex', flexDirection: 'column', gap: 2 },
  layerItem: {
    display: 'flex', alignItems: 'center', gap: 12, padding: '10px 8px',
    background: 'none', border: 'none', borderRadius: 'var(--radius-md)',
    cursor: 'pointer', textAlign: 'left', width: '100%',
    transition: 'var(--transition-fast)',
  },
  toggle: {
    width: 32, height: 18, borderRadius: 9, border: '1px solid',
    position: 'relative', flexShrink: 0,
    transition: 'var(--transition-fast)',
  },
  toggleDot: {
    width: 14, height: 14, borderRadius: '50%', position: 'absolute',
    top: 1, left: 1, transition: 'var(--transition-fast)',
  },
  layerLabel: {
    display: 'flex', alignItems: 'center', gap: 6,
    fontSize: 13, fontWeight: 500, color: 'var(--text-primary)',
  },
  layerDesc: {
    fontSize: 11, color: 'var(--text-muted)', marginTop: 2,
  },
};
