import { useState } from 'react';
import { useAlertStore } from '../state/alertStore';
import { useUIStore } from '../state/uiStore';
import type { Alert } from '../types/canonical';
import { severityColor, timeAgo } from '../utils/geo';
import { AlertTriangle, Bell, BellOff, ChevronRight, Shield } from 'lucide-react';

const SEVERITY_ICONS: Record<string, React.ReactNode> = {
  CRITICAL: <AlertTriangle size={14} />,
  HIGH: <AlertTriangle size={14} />,
  MEDIUM: <Shield size={14} />,
  LOW: <Bell size={14} />,
  INFO: <Bell size={14} />,
};

export function AlertPanel() {
  const alerts = useAlertStore((s) => s.sorted);
  const selectAlert = useAlertStore((s) => s.selectAlert);
  const selectedAlertId = useAlertStore((s) => s.selectedAlertId);
  const selectEntity = useUIStore((s) => s.selectEntity);
  const [filter, setFilter] = useState<string | null>(null);

  const filtered = filter ? alerts.filter((a) => a.severity === filter) : alerts;

  const handleClick = (alert: Alert) => {
    selectAlert(alert.alert_id);
    if (alert.affected_actor_ids.length > 0) {
      selectEntity(alert.affected_actor_ids[0], 'vehicle');
    }
  };

  if (alerts.length === 0) {
    return (
      <div style={styles.empty}>
        <BellOff size={32} style={{ color: 'var(--text-muted)' }} />
        <p style={{ color: 'var(--text-muted)', fontSize: 13, marginTop: 8 }}>No active alerts</p>
        <p style={{ color: 'var(--text-muted)', fontSize: 11 }}>System is monitoring</p>
      </div>
    );
  }

  return (
    <div>
      <div style={styles.filterRow}>
        {['CRITICAL', 'HIGH', 'MEDIUM', 'LOW'].map((sev) => {
          const count = alerts.filter((a) => a.severity === sev).length;
          return (
            <button
              key={sev}
              onClick={() => setFilter(filter === sev ? null : sev)}
              style={{
                ...styles.filterBtn,
                background: filter === sev ? severityColor(sev === 'CRITICAL' ? 0.9 : sev === 'HIGH' ? 0.7 : sev === 'MEDIUM' ? 0.5 : 0.3) : 'var(--bg-tertiary)',
                color: filter === sev ? '#fff' : 'var(--text-secondary)',
                opacity: count === 0 ? 0.4 : 1,
              }}
              disabled={count === 0}
            >
              {sev.slice(0, 4)} ({count})
            </button>
          );
        })}
      </div>

      <div style={styles.list}>
        {filtered.map((alert) => (
          <div
            key={alert.alert_id}
            onClick={() => handleClick(alert)}
            style={{
              ...styles.alertCard,
              borderLeftColor: severityColor(
                alert.severity === 'CRITICAL' ? 0.9 : alert.severity === 'HIGH' ? 0.7 : alert.severity === 'MEDIUM' ? 0.5 : 0.3,
              ),
              background: selectedAlertId === alert.alert_id ? 'var(--bg-tertiary)' : 'var(--bg-elevated)',
              animation: alert.severity === 'CRITICAL' ? 'pulse 2s ease-in-out infinite' : undefined,
            }}
          >
            <div style={styles.alertHeader}>
              <span style={{
                ...styles.severityBadge,
                background: severityColor(
                  alert.severity === 'CRITICAL' ? 0.9 : alert.severity === 'HIGH' ? 0.7 : alert.severity === 'MEDIUM' ? 0.5 : 0.3,
                ),
              }}>
                {SEVERITY_ICONS[alert.severity]}
                {alert.severity}
              </span>
              <span style={styles.alertTime}>{timeAgo(alert.issued_at)}</span>
            </div>
            <div style={styles.alertTitle}>{alert.title}</div>
            <div style={styles.alertDesc}>{alert.description}</div>
            <div style={styles.alertFooter}>
              <span style={styles.alertMeta}>
                Confidence: {(alert.confidence * 100).toFixed(0)}%
              </span>
              <span style={styles.alertMeta}>
                {alert.affected_actor_ids.length} actor{alert.affected_actor_ids.length !== 1 ? 's' : ''}
              </span>
              <ChevronRight size={14} style={{ color: 'var(--text-muted)', marginLeft: 'auto' }} />
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  empty: {
    display: 'flex', flexDirection: 'column', alignItems: 'center',
    justifyContent: 'center', padding: 40,
  },
  filterRow: { display: 'flex', gap: 4, marginBottom: 12 },
  filterBtn: {
    flex: 1, padding: '4px 6px', border: 'none', borderRadius: 'var(--radius-sm)',
    fontSize: 10, fontWeight: 600, cursor: 'pointer', letterSpacing: 0.3,
    transition: 'var(--transition-fast)',
  },
  list: { display: 'flex', flexDirection: 'column', gap: 8 },
  alertCard: {
    padding: 12, borderRadius: 'var(--radius-md)', borderLeft: '3px solid',
    cursor: 'pointer', transition: 'var(--transition-fast)',
  },
  alertHeader: {
    display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 6,
  },
  severityBadge: {
    display: 'inline-flex', alignItems: 'center', gap: 4, padding: '2px 8px',
    borderRadius: 'var(--radius-sm)', fontSize: 10, fontWeight: 700, color: '#fff',
    letterSpacing: 0.5,
  },
  alertTime: { fontSize: 11, color: 'var(--text-muted)' },
  alertTitle: { fontSize: 13, fontWeight: 600, color: 'var(--text-primary)', marginBottom: 4 },
  alertDesc: { fontSize: 12, color: 'var(--text-secondary)', lineHeight: 1.4, marginBottom: 8 },
  alertFooter: { display: 'flex', alignItems: 'center', gap: 12 },
  alertMeta: { fontSize: 11, color: 'var(--text-muted)' },
};
