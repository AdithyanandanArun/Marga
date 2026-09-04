import { useState, useEffect, useCallback } from 'react';
import { Link } from 'react-router-dom';
import { MapView } from '../map/MapView';
import { useWorldStore } from '../state/worldStore';
import { useAlertStore } from '../state/alertStore';
import { useUIStore } from '../state/uiStore';
import { formatSpeed, headingToCardinal, confidenceLabel, actorTypeIcon } from '../utils/geo';
import type { VehicleState, Alert } from '../types/canonical';
import {
  ArrowLeft,
  Compass,
  Gauge,
  Shield,
  AlertTriangle,
  CheckCircle,
  Wifi,
  WifiOff,
  ChevronDown,
  StopCircle,
  Play,
  Minus,
  Plus,
} from 'lucide-react';

const GATEWAY = '';

async function sendActorCommand(actorId: string, action: string, speedMps?: number) {
  await fetch(`${GATEWAY}/v1/world-state/actors/${encodeURIComponent(actorId)}/command`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ action, speed_mps: speedMps ?? null }),
  });
}

export function DriverConsole() {
  const vehicles = useWorldStore((s) => s.vehicles);
  const connectivity = useWorldStore((s) => s.connectivity);
  const alerts = useAlertStore((s) => s.sorted);
  const setViewport = useUIStore((s) => s.setViewport);

  const vehicleIds = Array.from(vehicles.keys());
  const [selectedVehicleId, setSelectedVehicleId] = useState<string>(vehicleIds[0] ?? '');

  useEffect(() => {
    if (!selectedVehicleId && vehicleIds.length > 0) {
      setSelectedVehicleId(vehicleIds[0]);
    }
  }, [vehicleIds, selectedVehicleId]);

  const vehicle = vehicles.get(selectedVehicleId);
  const vehicleAlerts = alerts.filter(
    (a) => a.affected_actor_ids.includes(selectedVehicleId),
  );

  useEffect(() => {
    if (vehicle) {
      setViewport({
        latitude: vehicle.position.lat,
        longitude: vehicle.position.lon,
        zoom: 16,
      });
    }
  }, [vehicle?.position.lat, vehicle?.position.lon]);

  const threatLevel = vehicleAlerts.some((a) => a.severity === 'CRITICAL')
    ? 'critical'
    : vehicleAlerts.some((a) => a.severity === 'HIGH')
      ? 'high'
      : vehicleAlerts.length > 0
        ? 'caution'
        : 'safe';

  const bgColor =
    threatLevel === 'critical' ? 'rgba(239,68,68,0.08)'
    : threatLevel === 'high' ? 'rgba(249,115,22,0.06)'
    : threatLevel === 'caution' ? 'rgba(234,179,8,0.04)'
    : 'rgba(34,197,94,0.03)';

  return (
    <div style={{ ...driverStyles.container, background: bgColor }}>
      <header style={driverStyles.header}>
        <Link to="/" style={driverStyles.backLink}><ArrowLeft size={18} /> Control Center</Link>
        <div style={driverStyles.headerCenter}>
          <Gauge size={18} style={{ color: 'var(--accent-blue)' }} />
          <span style={{ fontWeight: 700, fontSize: 15 }}>Driver Console</span>
        </div>
        <div style={driverStyles.headerRight}>
          <div style={{
            display: 'flex', alignItems: 'center', gap: 6, fontSize: 12,
            color: connectivity === 'FULL' ? 'var(--accent-green)' : 'var(--accent-yellow)',
          }}>
            {connectivity === 'FULL' ? <Wifi size={14} /> : <WifiOff size={14} />}
            {connectivity}
          </div>
        </div>
      </header>

      <div style={driverStyles.vehicleSelector}>
        <select
          value={selectedVehicleId}
          onChange={(e) => setSelectedVehicleId(e.target.value)}
          style={driverStyles.select}
        >
          {vehicleIds.map((id) => {
            const v = vehicles.get(id)!;
            return (
              <option key={id} value={id}>
                {actorTypeIcon(v.actor_type)} {id} — {v.actor_type}
              </option>
            );
          })}
        </select>
        <ChevronDown size={16} style={{ position: 'absolute', right: 12, top: '50%', transform: 'translateY(-50%)', color: 'var(--text-muted)', pointerEvents: 'none' }} />
      </div>

      <div style={driverStyles.main}>
        <div style={driverStyles.mapSection}>
          <MapView />
        </div>

        <div style={driverStyles.infoSection}>
          {vehicle ? (
            <VehicleHUD vehicle={vehicle} />
          ) : (
            <div style={{ textAlign: 'center', padding: 40, color: 'var(--text-muted)' }}>
              No vehicle selected
            </div>
          )}

          {vehicle && (
            <CommandPanel vehicleId={selectedVehicleId} speedMps={vehicle.speed_mps} />
          )}

          <div style={driverStyles.alertsSection}>
            <DriverAlerts alerts={vehicleAlerts} />
          </div>
        </div>
      </div>
    </div>
  );
}

function VehicleHUD({ vehicle: v }: { vehicle: VehicleState }) {
  const speedKmh = v.speed_mps * 3.6;
  const uncertaintyLevel = v.position_uncertainty_m < 5 ? 'high' : v.position_uncertainty_m < 15 ? 'medium' : 'low';

  return (
    <div style={driverStyles.hud}>
      <div style={driverStyles.speedSection}>
        <div style={driverStyles.speedValue}>{speedKmh.toFixed(0)}</div>
        <div style={driverStyles.speedUnit}>km/h</div>
      </div>

      <div style={driverStyles.hudInfo}>
        <div style={driverStyles.hudItem}>
          <Compass size={18} style={{ color: 'var(--accent-cyan)' }} />
          <div>
            <div style={driverStyles.hudLabel}>Heading</div>
            <div style={driverStyles.hudValue}>
              {v.heading_deg.toFixed(0)}° {headingToCardinal(v.heading_deg)}
            </div>
          </div>
        </div>

        <div style={driverStyles.hudItem}>
          <Shield size={18} style={{
            color: uncertaintyLevel === 'high' ? 'var(--accent-green)'
              : uncertaintyLevel === 'medium' ? 'var(--accent-yellow)'
              : 'var(--accent-red)'
          }} />
          <div>
            <div style={driverStyles.hudLabel}>Position</div>
            <div style={driverStyles.hudValue}>
              ±{v.position_uncertainty_m.toFixed(1)}m ({confidenceLabel(1 - v.position_uncertainty_m / 30)})
            </div>
          </div>
        </div>

        <div style={driverStyles.hudItem}>
          <Gauge size={18} style={{ color: 'var(--accent-purple)' }} />
          <div>
            <div style={driverStyles.hudLabel}>Type</div>
            <div style={driverStyles.hudValue}>
              {actorTypeIcon(v.actor_type)} {v.actor_type}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function CommandPanel({ vehicleId, speedMps }: { vehicleId: string; speedMps: number }) {
  const [sending, setSending] = useState(false);

  const send = useCallback(async (action: string, speed?: number) => {
    setSending(true);
    try { await sendActorCommand(vehicleId, action, speed); } finally { setSending(false); }
  }, [vehicleId]);

  const step = 5 / 3.6; // 5 km/h in m/s
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      <div style={{ fontSize: 10, fontWeight: 600, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: 0.5 }}>
        Vehicle Commands
      </div>
      <div style={{ display: 'flex', gap: 6 }}>
        <button disabled={sending} onClick={() => send('set_speed', Math.max(0, speedMps - step))} style={cmdStyle}>
          <Minus size={14} /> 5 km/h
        </button>
        <button disabled={sending} onClick={() => send('set_speed', speedMps + step)} style={cmdStyle}>
          <Plus size={14} /> 5 km/h
        </button>
        <button disabled={sending} onClick={() => send('stop')} style={{ ...cmdStyle, color: 'var(--accent-red)' }}>
          <StopCircle size={14} /> Stop
        </button>
        <button disabled={sending} onClick={() => send('resume')} style={{ ...cmdStyle, color: 'var(--accent-green)' }}>
          <Play size={14} /> Resume
        </button>
      </div>
    </div>
  );
}

const cmdStyle: React.CSSProperties = {
  display: 'flex', alignItems: 'center', gap: 4, padding: '6px 10px',
  background: 'var(--bg-elevated)', border: '1px solid var(--border-primary)',
  borderRadius: 'var(--radius-sm)', color: 'var(--text-primary)',
  fontSize: 12, fontWeight: 500, cursor: 'pointer',
};

function DriverAlerts({ alerts }: { alerts: Alert[] }) {
  const topAlerts = alerts.slice(0, 3);

  if (topAlerts.length === 0) {
    return (
      <div style={driverStyles.allClear}>
        <CheckCircle size={28} style={{ color: 'var(--accent-green)' }} />
        <span style={{ fontSize: 16, fontWeight: 600, color: 'var(--accent-green)' }}>All Clear</span>
        <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>No active safety alerts</span>
      </div>
    );
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      {topAlerts.map((alert) => {
        const isCritical = alert.severity === 'CRITICAL';
        return (
          <div
            key={alert.alert_id}
            data-severity={alert.severity}
            style={{
              ...driverStyles.alertCard,
              background: isCritical ? 'rgba(239,68,68,0.2)' : 'var(--bg-elevated)',
              borderColor: isCritical ? 'var(--severity-critical)' : alert.severity === 'HIGH' ? 'var(--severity-high)' : 'var(--severity-medium)',
              animation: isCritical ? 'pulse 1.5s ease-in-out infinite' : undefined,
            }}
          >
            <AlertTriangle size={20} style={{
              color: isCritical ? 'var(--severity-critical)' : alert.severity === 'HIGH' ? 'var(--severity-high)' : 'var(--severity-medium)',
              flexShrink: 0,
            }} />
            <div>
              <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--text-primary)' }}>{alert.title}</div>
              <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginTop: 2 }}>{alert.description}</div>
            </div>
          </div>
        );
      })}
    </div>
  );
}

const driverStyles: Record<string, React.CSSProperties> = {
  container: { display: 'flex', flexDirection: 'column', height: '100vh', overflow: 'hidden' },
  header: {
    display: 'flex', alignItems: 'center', height: 48, padding: '0 16px',
    background: 'var(--bg-secondary)', borderBottom: '1px solid var(--border-primary)',
    flexShrink: 0,
  },
  backLink: {
    display: 'flex', alignItems: 'center', gap: 6, color: 'var(--text-secondary)',
    textDecoration: 'none', fontSize: 13, fontWeight: 500,
  },
  headerCenter: { display: 'flex', alignItems: 'center', gap: 8, margin: '0 auto' },
  headerRight: { display: 'flex', alignItems: 'center' },
  vehicleSelector: {
    position: 'relative', padding: '8px 16px', background: 'var(--bg-tertiary)',
    borderBottom: '1px solid var(--border-primary)', flexShrink: 0,
  },
  select: {
    width: '100%', padding: '8px 32px 8px 12px', appearance: 'none',
    background: 'var(--bg-secondary)', border: '1px solid var(--border-primary)',
    borderRadius: 'var(--radius-md)', color: 'var(--text-primary)',
    fontSize: 13, fontFamily: 'var(--font-mono)', cursor: 'pointer',
  },
  main: { flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' },
  mapSection: { flex: 3, position: 'relative', minHeight: 0 },
  infoSection: { flex: 2, display: 'flex', flexDirection: 'column', overflow: 'auto', padding: 16, gap: 16 },
  hud: { display: 'flex', gap: 24, alignItems: 'center' },
  speedSection: { display: 'flex', alignItems: 'baseline', gap: 4, flexShrink: 0 },
  speedValue: { fontSize: 56, fontWeight: 800, color: 'var(--text-primary)', fontFamily: 'var(--font-mono)', lineHeight: 1 },
  speedUnit: { fontSize: 16, color: 'var(--text-muted)', fontWeight: 500 },
  hudInfo: { display: 'flex', flexDirection: 'column', gap: 8, flex: 1 },
  hudItem: { display: 'flex', alignItems: 'center', gap: 10 },
  hudLabel: { fontSize: 10, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: 0.5 },
  hudValue: { fontSize: 14, fontWeight: 600, color: 'var(--text-primary)', fontFamily: 'var(--font-mono)' },
  alertsSection: { flex: 1 },
  allClear: {
    display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
    padding: 24, gap: 4,
  },
  alertCard: {
    display: 'flex', alignItems: 'flex-start', gap: 12, padding: 14,
    borderRadius: 'var(--radius-md)', borderLeft: '3px solid',
  },
};
