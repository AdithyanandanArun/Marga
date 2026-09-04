import { useWorldStore } from '../state/worldStore';
import { Activity, Clock, Zap, Shield, Wifi, AlertTriangle, Radio } from 'lucide-react';

export function SystemHealth() {
  const metrics = useWorldStore((s) => s.metrics);
  const connectivity = useWorldStore((s) => s.connectivity);

  if (!metrics) {
    return (
      <div style={styles.empty}>
        <Activity size={32} style={{ color: 'var(--text-muted)' }} />
        <p style={{ color: 'var(--text-muted)', fontSize: 13, marginTop: 8 }}>Awaiting metrics...</p>
      </div>
    );
  }

  return (
    <div>
      <div style={styles.grid}>
        <MetricCard
          icon={<Activity size={14} />}
          label="Actor Updates"
          value={`${metrics.actor_updates_per_sec}`}
          unit="/sec"
          color="var(--accent-blue)"
        />
        <MetricCard
          icon={<Zap size={14} />}
          label="Risk Evals"
          value={`${metrics.risk_evaluations_per_sec}`}
          unit="/sec"
          color="var(--accent-purple)"
        />
        <MetricCard
          icon={<Clock size={14} />}
          label="Bus Lag"
          value={`${metrics.event_bus_lag_ms}`}
          unit="ms"
          color={metrics.event_bus_lag_ms < 10 ? 'var(--accent-green)' : metrics.event_bus_lag_ms < 50 ? 'var(--accent-yellow)' : 'var(--accent-red)'}
        />
        <MetricCard
          icon={<AlertTriangle size={14} />}
          label="Alerts"
          value={`${metrics.alerts_issued}`}
          unit={`/ ${metrics.alerts_cleared} cleared`}
          color="var(--accent-orange)"
        />
      </div>

      <div style={styles.section}>
        <div style={styles.sectionTitle}>Risk Latency</div>
        <LatencyBar label="p50" value={metrics.risk_p50_ms} max={100} />
        <LatencyBar label="p95" value={metrics.risk_p95_ms} max={100} />
        <LatencyBar label="p99" value={metrics.risk_p99_ms} max={100} />
      </div>

      <div style={styles.section}>
        <div style={styles.sectionTitle}>System State</div>
        <div style={styles.stateGrid}>
          <StateRow icon={<Wifi size={14} />} label="Connectivity" value={connectivity}
            color={connectivity === 'FULL' ? 'var(--accent-green)' : 'var(--accent-yellow)'} />
          <StateRow icon={<Radio size={14} />} label="WS Clients" value={`${metrics.ws_clients}`}
            color="var(--accent-blue)" />
          <StateRow icon={<Shield size={14} />} label="Trust Rejections" value={`${metrics.trust_rejections}`}
            color={metrics.trust_rejections > 0 ? 'var(--accent-red)' : 'var(--accent-green)'} />
          <StateRow icon={<Zap size={14} />} label="Sim Speed" value={`${metrics.simulation_speed}x`}
            color="var(--accent-cyan)" />
          <StateRow icon={<Activity size={14} />} label="Dropped Msgs" value={`${metrics.dropped_messages}`}
            color={metrics.dropped_messages > 0 ? 'var(--accent-yellow)' : 'var(--accent-green)'} />
          <StateRow icon={<Activity size={14} />} label="Uncertainty Avg" value={`±${metrics.position_uncertainty_avg.toFixed(1)}m`}
            color={metrics.position_uncertainty_avg < 5 ? 'var(--accent-green)' : 'var(--accent-yellow)'} />
        </div>
      </div>
    </div>
  );
}

function MetricCard({ icon, label, value, unit, color }: {
  icon: React.ReactNode; label: string; value: string; unit: string; color: string;
}) {
  return (
    <div style={styles.metricCard}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 6 }}>
        <span style={{ color }}>{icon}</span>
        <span style={{ fontSize: 10, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: 0.5 }}>{label}</span>
      </div>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 4 }}>
        <span style={{ fontSize: 22, fontWeight: 700, color, fontFamily: 'var(--font-mono)' }}>{value}</span>
        <span style={{ fontSize: 10, color: 'var(--text-muted)' }}>{unit}</span>
      </div>
    </div>
  );
}

function LatencyBar({ label, value, max }: { label: string; value: number; max: number }) {
  const pct = Math.min((value / max) * 100, 100);
  const color = value < 20 ? 'var(--accent-green)' : value < 50 ? 'var(--accent-yellow)' : 'var(--accent-red)';
  return (
    <div style={{ marginBottom: 8 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 3 }}>
        <span style={{ fontSize: 11, color: 'var(--text-secondary)' }}>{label}</span>
        <span style={{ fontSize: 11, color, fontWeight: 600, fontFamily: 'var(--font-mono)' }}>{value}ms</span>
      </div>
      <div style={{ height: 4, borderRadius: 2, background: 'var(--bg-primary)' }}>
        <div style={{
          height: '100%', width: `${pct}%`, borderRadius: 2,
          background: color, transition: 'width 0.3s',
        }} />
      </div>
    </div>
  );
}

function StateRow({ icon, label, value, color }: {
  icon: React.ReactNode; label: string; value: string; color: string;
}) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '6px 0' }}>
      <span style={{ color: 'var(--text-muted)' }}>{icon}</span>
      <span style={{ fontSize: 12, color: 'var(--text-secondary)', flex: 1 }}>{label}</span>
      <span style={{ fontSize: 12, fontWeight: 600, color, fontFamily: 'var(--font-mono)' }}>{value}</span>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  empty: {
    display: 'flex', flexDirection: 'column', alignItems: 'center',
    justifyContent: 'center', padding: 40,
  },
  grid: {
    display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8, marginBottom: 16,
  },
  metricCard: {
    padding: 12, borderRadius: 'var(--radius-md)', background: 'var(--bg-elevated)',
  },
  section: {
    marginBottom: 16, paddingBottom: 12, borderBottom: '1px solid var(--border-primary)',
  },
  sectionTitle: {
    fontSize: 11, fontWeight: 600, color: 'var(--text-secondary)',
    marginBottom: 10, textTransform: 'uppercase', letterSpacing: 0.5,
  },
  stateGrid: {
    display: 'flex', flexDirection: 'column',
  },
};
