import { useEffect, useState } from 'react';
import { useUIStore } from '../state/uiStore';
import { useWorldStore } from '../state/worldStore';
import { useGraphStore } from '../state/graphStore';
import { useRouteStore } from '../state/routeStore';
import { fetchGraphEdge } from '../net/graphClient';
import { fetchSignalState, requestSignalRecommendation } from '../net/signalsClient';
import { formatSpeed, formatCoord, confidenceLabel, timeAgo, actorTypeIcon, headingToCardinal } from '../utils/geo';
import { X, Navigation, Gauge, MapPin, Shield, Radio, Eye, TrafficCone, Network, Brain, Route } from 'lucide-react';
import type { VehicleState, Hazard, TrafficSignalState, RSUState } from '../types/canonical';
import type { GraphEdgeMetrics } from '../types/graph';
import type { RouteChange } from '../types/routes';
import type { SignalJunctionState, SignalRecommendation } from '../types/signals';

export function Inspector() {
  const selectedId = useUIStore((s) => s.selectedEntityId);
  const selectedType = useUIStore((s) => s.selectedEntityType);
  const selectEntity = useUIStore((s) => s.selectEntity);

  const vehicles = useWorldStore((s) => s.vehicles);
  const hazards = useWorldStore((s) => s.hazards);
  const signals = useWorldStore((s) => s.signals);
  const rsus = useWorldStore((s) => s.rsus);
  const graphEdges = useGraphStore((s) => s.edges);
  const routeChanges = useRouteStore((s) => s.changesByVehicle);

  if (!selectedId) {
    return (
      <div style={styles.empty}>
        <Eye size={32} style={{ color: 'var(--text-muted)' }} />
        <p style={{ color: 'var(--text-muted)', fontSize: 13, marginTop: 8 }}>No entity selected</p>
        <p style={{ color: 'var(--text-muted)', fontSize: 11 }}>Click an entity on the map</p>
      </div>
    );
  }

  const close = () => selectEntity(null, null);

  if (selectedType === 'vehicle') {
    const v = vehicles.get(selectedId);
    if (!v) return <div style={styles.empty}>Vehicle not found</div>;
    return <VehicleInspector vehicle={v} reroute={routeChanges.get(v.actor_id)} onClose={close} />;
  }

  if (selectedType === 'hazard') {
    const h = hazards.get(selectedId);
    if (!h) return <div style={styles.empty}>Hazard not found</div>;
    return <HazardInspector hazard={h} onClose={close} />;
  }

  if (selectedType === 'signal') {
    const s = signals.get(selectedId);
    if (!s) return <div style={styles.empty}>Signal not found</div>;
    return <SignalInspector signal={s} onClose={close} />;
  }

  if (selectedType === 'rsu') {
    const r = rsus.get(selectedId);
    if (!r) return <div style={styles.empty}>RSU not found</div>;
    return <RSUInspector rsu={r} onClose={close} />;
  }

  if (selectedType === 'edge') {
    return <EdgeInspector edgeId={selectedId} edge={graphEdges.get(selectedId)} onClose={close} />;
  }

  return <div style={styles.empty}>Unknown entity type</div>;
}

function VehicleInspector({ vehicle: v, reroute, onClose }: { vehicle: VehicleState; reroute?: RouteChange; onClose: () => void }) {
  return (
    <div>
      <div style={styles.inspectorHeader}>
        <span style={{ fontSize: 20 }}>{actorTypeIcon(v.actor_type)}</span>
        <div>
          <div style={styles.entityId}>{v.actor_id}</div>
          <div style={styles.entityType}>{v.actor_type}</div>
        </div>
        <button onClick={onClose} style={styles.closeBtn}><X size={16} /></button>
      </div>

      <div style={stateBadgeStyle(v.lifecycle ?? 'ACTIVE')}>
        {v.lifecycle ?? 'ACTIVE'}
      </div>

      <div style={styles.section}>
        <div style={styles.sectionTitle}><Gauge size={14} /> Motion</div>
        <div style={styles.statGrid}>
          <StatItem label="Speed" value={formatSpeed(v.speed_mps)} />
          <StatItem label="Heading" value={`${v.heading_deg.toFixed(0)}° ${headingToCardinal(v.heading_deg)}`} />
          {v.acceleration_mps2 !== undefined && (
            <StatItem label="Accel" value={`${v.acceleration_mps2.toFixed(1)} m/s²`} />
          )}
          {v.yaw_rate_dps !== undefined && (
            <StatItem label="Yaw Rate" value={`${v.yaw_rate_dps.toFixed(1)}°/s`} />
          )}
        </div>
      </div>

      <div style={styles.section}>
        <div style={styles.sectionTitle}><MapPin size={14} /> Position</div>
        <div style={styles.statGrid}>
          <StatItem label="Coordinates" value={formatCoord(v.position.lat, v.position.lon)} wide />
          <StatItem label="Uncertainty" value={`±${v.position_uncertainty_m.toFixed(1)} m`}
            color={v.position_uncertainty_m < 5 ? 'var(--confidence-high)' : v.position_uncertainty_m < 15 ? 'var(--confidence-medium)' : 'var(--confidence-low)'} />
          <StatItem label="Source" value={v.source} />
        </div>
      </div>

      {v.road_segment_id && (
        <div style={styles.section}>
          <div style={styles.sectionTitle}><Navigation size={14} /> Road Context</div>
          <div style={styles.statGrid}>
            <StatItem label="Segment" value={v.road_segment_id} wide />
            {v.lane_id && <StatItem label="Lane" value={v.lane_id} />}
          </div>
        </div>
      )}

      {reroute && (
        <div style={styles.section}>
          <div style={styles.sectionTitle}><Route size={14} /> Cooperative Reroute</div>
          <div style={styles.statGrid}>
            <StatItem label="Old ETA" value={`${reroute.old_eta_s.toFixed(0)}s`} />
            <StatItem
              label="New ETA"
              value={`${reroute.new_eta_s.toFixed(0)}s (${reroute.new_eta_s <= reroute.old_eta_s ? '−' : '+'}${Math.abs(reroute.old_eta_s - reroute.new_eta_s).toFixed(0)}s)`}
              color={reroute.new_eta_s < reroute.old_eta_s ? 'var(--accent-green)' : 'var(--accent-yellow)'}
            />
            <StatItem label="Reason" value={reroute.reason} wide />
            <StatItem label="Changed" value={timeAgo(reroute.changed_at)} />
          </div>
        </div>
      )}

      <div style={styles.section}>
        <div style={styles.sectionTitle}><Shield size={14} /> Trust & Capabilities</div>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
          {v.capabilities.map((cap) => (
            <span key={cap} style={styles.capBadge}>{cap}</span>
          ))}
        </div>
      </div>
    </div>
  );
}

function HazardInspector({ hazard: h, onClose }: { hazard: Hazard; onClose: () => void }) {
  return (
    <div>
      <div style={styles.inspectorHeader}>
        <span style={{ fontSize: 20 }}>{'⚠️'}</span>
        <div>
          <div style={styles.entityId}>{h.hazard_id}</div>
          <div style={styles.entityType}>{h.type}</div>
        </div>
        <button onClick={onClose} style={styles.closeBtn}><X size={16} /></button>
      </div>

      <div style={stateBadgeStyle(h.state === 'VERIFIED' ? 'ACTIVE' : h.state === 'STALE' ? 'STALE' : 'NEW')}>
        {h.state}
      </div>

      <div style={styles.section}>
        <ProgressBar label="Severity" value={h.severity} color="var(--severity-high)" />
        <ProgressBar label="Confidence" value={h.confidence}
          color={h.confidence > 0.7 ? 'var(--confidence-high)' : h.confidence > 0.4 ? 'var(--confidence-medium)' : 'var(--confidence-low)'} />
      </div>

      <div style={styles.section}>
        <div style={styles.statGrid}>
          <StatItem label="Evidence" value={`${h.evidence_count} reports`} />
          <StatItem label="TTL" value={`${h.ttl_s}s`} />
          <StatItem label="First Seen" value={timeAgo(h.first_seen)} />
          <StatItem label="Last Seen" value={timeAgo(h.last_seen)} />
          <StatItem label="Sources" value={`${h.source_ids.length}`} />
        </div>
      </div>
    </div>
  );
}

function SignalInspector({ signal: s, onClose }: { signal: TrafficSignalState; onClose: () => void }) {
  const phaseColors: Record<string, string> = { RED: '#ef4444', AMBER: '#eab308', GREEN: '#22c55e' };
  const phases = s.phases ?? Object.entries(s.movements ?? {}).map(([movement_id, state]) => ({ movement_id, state }));
  const junctionId = s.intersection_id ?? s.junction_id ?? null;

  const [rlState, setRlState] = useState<SignalJunctionState | null>(null);
  const [recommendation, setRecommendation] = useState<SignalRecommendation | null>(null);
  const [loadingRl, setLoadingRl] = useState(false);

  useEffect(() => {
    if (!junctionId) return;
    let cancelled = false;
    setRlState(null);
    setRecommendation(null);
    fetchSignalState(junctionId).then((state) => { if (!cancelled) setRlState(state); });
    return () => { cancelled = true; };
  }, [junctionId]);

  const askForRecommendation = () => {
    if (!junctionId) return;
    setLoadingRl(true);
    requestSignalRecommendation(junctionId)
      .then((rec) => setRecommendation(rec))
      .finally(() => setLoadingRl(false));
  };

  return (
    <div>
      <div style={styles.inspectorHeader}>
        <TrafficCone size={20} style={{ color: 'var(--accent-yellow)' }} />
        <div>
          <div style={styles.entityId}>{s.signal_id}</div>
          <div style={styles.entityType}>Traffic Signal</div>
        </div>
        <button onClick={onClose} style={styles.closeBtn}><X size={16} /></button>
      </div>

      <div style={styles.section}>
        <div style={styles.sectionTitle}>Phases</div>
        {phases.map((p) => (
          <div key={p.movement_id} style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
            <div style={{ width: 12, height: 12, borderRadius: '50%', background: phaseColors[p.state] ?? '#888' }} />
            <span style={{ fontSize: 13, color: 'var(--text-primary)' }}>{p.movement_id}</span>
            <span style={{ fontSize: 12, color: 'var(--text-secondary)', marginLeft: 'auto' }}>{p.state}</span>
          </div>
        ))}
      </div>

      <div style={styles.section}>
        <div style={styles.statGrid}>
          <StatItem label="Junction" value={junctionId ?? '—'} />
          <StatItem label="Mode" value={s.controller_mode ?? 'ACTUATED'} />
          <StatItem label="Confidence" value={`${(s.confidence * 100).toFixed(0)}%`} />
        </div>
      </div>

      <div style={styles.section}>
        <div style={styles.sectionTitle}><Brain size={14} /> RL Signal Controller</div>
        {!junctionId ? (
          <p style={{ fontSize: 12, color: 'var(--text-muted)' }}>No junction id on this signal — cannot query the RL controller.</p>
        ) : rlState ? (
          <>
            <div style={styles.statGrid}>
              <StatItem label="Current Phase" value={rlState.current_phase} />
              <StatItem label="Phase Duration" value={`${rlState.phase_duration_s.toFixed(1)}s`} />
            </div>
            {rlState.approaches.length > 0 && (
              <div style={{ marginTop: 10 }}>
                {rlState.approaches.map((a) => (
                  <div key={a.approach_id} style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, marginBottom: 4 }}>
                    <span style={{ color: 'var(--text-secondary)', fontFamily: 'var(--font-mono)' }}>{a.approach_id}</span>
                    <span style={{ color: 'var(--text-primary)' }}>queue {a.queue_length} · {a.avg_speed_mps.toFixed(1)} m/s</span>
                  </div>
                ))}
              </div>
            )}
            {(rlState.last_recommendation ?? recommendation) && (
              <div style={{ marginTop: 10, padding: 8, borderRadius: 'var(--radius-sm)', background: 'var(--bg-primary)' }}>
                <div style={{ fontSize: 11, fontWeight: 700, color: 'var(--accent-purple)' }}>
                  {(recommendation ?? rlState.last_recommendation)!.action}
                </div>
                <div style={{ fontSize: 11, color: 'var(--text-secondary)', marginTop: 2 }}>
                  {(recommendation ?? rlState.last_recommendation)!.reason}
                </div>
              </div>
            )}
            {rlState.last_outcome && (
              <div style={{ marginTop: 8, fontSize: 11, color: 'var(--text-secondary)' }}>
                Outcome: queue {rlState.last_outcome.queue_before} → {rlState.last_outcome.queue_after}
              </div>
            )}
            <button onClick={askForRecommendation} disabled={loadingRl} style={styles.smallActionBtn}>
              {loadingRl ? 'Requesting…' : 'Request recommendation'}
            </button>
          </>
        ) : (
          <p style={{ fontSize: 12, color: 'var(--text-muted)' }}>Waiting for the RL signal service — no fabricated decision shown.</p>
        )}
      </div>
    </div>
  );
}

function EdgeInspector({ edgeId, edge, onClose }: { edgeId: string; edge: GraphEdgeMetrics | undefined; onClose: () => void }) {
  const [fetched, setFetched] = useState<GraphEdgeMetrics | null>(null);
  const [loading, setLoading] = useState(!edge);

  useEffect(() => {
    if (edge) return;
    let cancelled = false;
    setLoading(true);
    fetchGraphEdge(edgeId).then((result) => {
      if (!cancelled) { setFetched(result); setLoading(false); }
    });
    return () => { cancelled = true; };
  }, [edgeId, edge]);

  const data = edge ?? fetched ?? undefined;

  return (
    <div>
      <div style={styles.inspectorHeader}>
        <Network size={20} style={{ color: 'var(--accent-blue)' }} />
        <div>
          <div style={styles.entityId}>{edgeId}</div>
          <div style={styles.entityType}>Road Edge</div>
        </div>
        <button onClick={onClose} style={styles.closeBtn}><X size={16} /></button>
      </div>

      {!data ? (
        <p style={{ fontSize: 12, color: 'var(--text-muted)' }}>
          {loading ? 'Loading graph metrics…' : 'Waiting for the mobility graph service — no fabricated metrics shown.'}
        </p>
      ) : (
        <>
          <div style={styles.section}>
            <ProgressBar label="Density" value={data.density} color={data.density > 0.7 ? 'var(--severity-high)' : data.density > 0.4 ? 'var(--severity-medium)' : 'var(--severity-low)'} />
            <ProgressBar label="Capacity" value={data.capacity_ratio} color={data.capacity_ratio > 0.8 ? 'var(--severity-high)' : data.capacity_ratio > 0.5 ? 'var(--severity-medium)' : 'var(--severity-low)'} />
          </div>
          <div style={styles.section}>
            <div style={styles.statGrid}>
              <StatItem label="Vehicles" value={`${data.vehicle_count}`} />
              <StatItem label="Queue" value={`${data.queue_length}`} />
              <StatItem label="Avg Speed" value={formatSpeed(data.avg_speed_mps)} />
              <StatItem label="Two-Wheeler Ratio" value={`${(data.two_wheeler_ratio * 100).toFixed(0)}%`} />
              <StatItem label="Flow Rate" value={`${data.flow_rate_vph.toFixed(0)} vph`} />
              <StatItem label="Occupancy" value={`${(data.occupancy * 100).toFixed(0)}%`} />
              <StatItem label="Downstream" value={`${(data.downstream_congestion * 100).toFixed(0)}%`} />
              <StatItem label="GPS Confidence" value={confidenceLabel(data.gps_confidence)} />
              {data.hazard_penalty > 0 && <StatItem label="Hazard Penalty" value={data.hazard_penalty.toFixed(2)} color="var(--severity-high)" />}
            </div>
          </div>
          {data.rolling_windows['60'] && (
            <div style={styles.section}>
              <div style={styles.sectionTitle}>60s Rolling Window</div>
              <div style={styles.statGrid}>
                <StatItem label="Avg Speed" value={formatSpeed(data.rolling_windows['60'].avg_speed_mps)} />
                <StatItem label="Vehicles" value={data.rolling_windows['60'].avg_vehicle_count.toFixed(1)} />
                <StatItem label="Queue" value={data.rolling_windows['60'].avg_queue_length.toFixed(1)} />
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}

function RSUInspector({ rsu: r, onClose }: { rsu: RSUState; onClose: () => void }) {
  return (
    <div>
      <div style={styles.inspectorHeader}>
        <Radio size={20} style={{ color: 'var(--accent-purple)' }} />
        <div>
          <div style={styles.entityId}>{r.rsu_id}</div>
          <div style={styles.entityType}>Roadside Unit</div>
        </div>
        <button onClick={onClose} style={styles.closeBtn}><X size={16} /></button>
      </div>

      <div style={styles.section}>
        <div style={styles.statGrid}>
          <StatItem label="Coverage" value={`${r.coverage_m.toFixed(0)} m`} />
          <StatItem label="Link State" value={r.link_state}
            color={r.link_state === 'FULL' ? 'var(--accent-green)' : 'var(--accent-yellow)'} />
          <StatItem label="Heartbeat" value={timeAgo(r.last_heartbeat)} />
          <StatItem label="Position" value={formatCoord(r.position.lat, r.position.lon)} wide />
        </div>
      </div>

      <div style={styles.section}>
        <div style={styles.sectionTitle}>Capabilities</div>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
          {r.capabilities.map((cap) => (
            <span key={cap} style={styles.capBadge}>{cap}</span>
          ))}
        </div>
      </div>
    </div>
  );
}

function StatItem({ label, value, color, wide }: { label: string; value: string; color?: string; wide?: boolean }) {
  return (
    <div style={{ gridColumn: wide ? '1 / -1' : undefined }}>
      <div style={{ fontSize: 10, color: 'var(--text-muted)', marginBottom: 2, textTransform: 'uppercase', letterSpacing: 0.5 }}>{label}</div>
      <div style={{ fontSize: 13, fontWeight: 500, color: color ?? 'var(--text-primary)', fontFamily: 'var(--font-mono)' }}>{value}</div>
    </div>
  );
}

function ProgressBar({ label, value, color }: { label: string; value: number; color: string }) {
  return (
    <div style={{ marginBottom: 10 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
        <span style={{ fontSize: 11, color: 'var(--text-secondary)' }}>{label}</span>
        <span style={{ fontSize: 11, color, fontWeight: 600 }}>{(value * 100).toFixed(0)}%</span>
      </div>
      <div style={{ height: 4, borderRadius: 2, background: 'var(--bg-primary)' }}>
        <div style={{ height: '100%', width: `${value * 100}%`, borderRadius: 2, background: color, transition: 'width 0.3s' }} />
      </div>
    </div>
  );
}

function stateBadgeStyle(lifecycle: string): React.CSSProperties {
  return {
    display: 'inline-block', padding: '3px 10px', borderRadius: 'var(--radius-sm)',
    fontSize: 10, fontWeight: 700, letterSpacing: 0.5, marginBottom: 12,
    background: lifecycle === 'ACTIVE' ? 'rgba(34,197,94,0.15)' : lifecycle === 'DEGRADED' ? 'rgba(234,179,8,0.15)' : lifecycle === 'STALE' ? 'rgba(155,161,176,0.15)' : 'rgba(74,125,255,0.15)',
    color: lifecycle === 'ACTIVE' ? 'var(--accent-green)' : lifecycle === 'DEGRADED' ? 'var(--accent-yellow)' : lifecycle === 'STALE' ? 'var(--text-muted)' : 'var(--accent-blue)',
  };
}

const styles: Record<string, React.CSSProperties> = {
  empty: {
    display: 'flex', flexDirection: 'column', alignItems: 'center',
    justifyContent: 'center', padding: 40,
  },
  inspectorHeader: {
    display: 'flex', alignItems: 'center', gap: 10, marginBottom: 12,
  },
  entityId: { fontSize: 14, fontWeight: 600, color: 'var(--text-primary)', fontFamily: 'var(--font-mono)' },
  entityType: { fontSize: 11, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: 0.5 },
  closeBtn: {
    marginLeft: 'auto', background: 'none', border: 'none', color: 'var(--text-muted)',
    cursor: 'pointer', padding: 4,
  },
  section: {
    marginBottom: 16, paddingBottom: 12, borderBottom: '1px solid var(--border-primary)',
  },
  sectionTitle: {
    display: 'flex', alignItems: 'center', gap: 6, fontSize: 11, fontWeight: 600,
    color: 'var(--text-secondary)', marginBottom: 10, textTransform: 'uppercase', letterSpacing: 0.5,
  },
  statGrid: {
    display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '10px 16px',
  },
  capBadge: {
    padding: '2px 8px', borderRadius: 'var(--radius-sm)', background: 'var(--bg-primary)',
    fontSize: 10, color: 'var(--text-secondary)', fontFamily: 'var(--font-mono)',
  },
  smallActionBtn: {
    marginTop: 10, padding: '6px 12px', borderRadius: 'var(--radius-sm)',
    background: 'var(--accent-purple)', border: 'none', color: '#fff',
    fontSize: 11, fontWeight: 600, cursor: 'pointer',
  },
};
