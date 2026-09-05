import { useCallback, useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import maplibregl from 'maplibre-gl';
import { Deck } from '@deck.gl/core';
import { PathLayer, PolygonLayer } from '@deck.gl/layers';
import { ArrowLeft, CheckCircle2, Pause, Play, Radio, RotateCcw, TrainTrack } from 'lucide-react';
import type { VehicleState, TrafficSignalState } from '../types/canonical';
import { createActorLayer } from '../map/layers/actors';
import { createInfrastructureLayer } from '../map/layers/infrastructure';
import {
  BED_COLOR,
  SURFACE_COLOR,
  LANE_LINE_COLOR,
  RAIL_TRACK_COLOR,
  SLEEPER_TIE_COLOR,
  type Point,
} from './junctionDefs';
import { buildJunctionNetwork, JunctionNetworkEngine, type JunctionNetwork } from './networkEngine';

// The display geometry is a deterministic local network. Its state is emitted
// through the canonical gateway ingestion contract, exactly like any simulator
// adapter, so the control center receives the same road users and signals.
const SIM_LAT = 12.9550;
const SIM_LON = 77.6200;

const SIM_STYLE: maplibregl.StyleSpecification = {
  version: 8,
  sources: {},
  layers: [{ id: 'sim-bg', type: 'background', paint: { 'background-color': '#0d1017' } }],
};

const ACTOR_TYPE_FOR_ADAPTER: Record<VehicleState['actor_type'], string> = {
  CAR: 'car', BIKE: 'motorcycle', AUTO: 'auto_rickshaw', BUS: 'bus', TRUCK: 'truck', AMBULANCE: 'emergency', OTHER: 'other',
};

export function SimulationStudio() {
  const mapContainer = useRef<HTMLDivElement>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const deckRef = useRef<Deck | null>(null);
  const engineRef = useRef<JunctionNetworkEngine | null>(null);
  const networkRef = useRef<JunctionNetwork | null>(null);
  const animRef = useRef<number>(0);
  const lastTsRef = useRef<number>(0);
  const playingRef = useRef(true);
  const lastPublishRef = useRef(0);
  const publishingRef = useRef(false);

  const [vehicleCount, setVehicleCount] = useState(24);
  const [chaos, setChaos] = useState(0.5);
  const [playing, setPlaying] = useState(true);
  const [feedState, setFeedState] = useState<'connecting' | 'live' | 'offline'>('connecting');

  useEffect(() => { playingRef.current = playing; }, [playing]);

  const resetNetwork = useCallback(() => {
    engineRef.current?.reset(vehicleCount);
    const network = networkRef.current;
    if (network) mapRef.current?.flyTo({ center: network.center, zoom: 15.35, duration: 650, essential: true });
  }, [vehicleCount]);

  useEffect(() => {
    if (!mapContainer.current) return;

    const network = buildJunctionNetwork(SIM_LAT, SIM_LON);
    networkRef.current = network;
    engineRef.current = new JunctionNetworkEngine(network, vehicleCount, chaos);

    const map = new maplibregl.Map({
      container: mapContainer.current,
      style: SIM_STYLE,
      center: network.center,
      zoom: 15.35,
      antialias: true,
    });
    map.addControl(new maplibregl.NavigationControl(), 'top-right');
    mapRef.current = map;

    const deck = new Deck({
      parent: mapContainer.current,
      style: { position: 'absolute', top: '0', left: '0', zIndex: '1', pointerEvents: 'none' },
      viewState: { latitude: network.center[1], longitude: network.center[0], zoom: 15.35, bearing: 0, pitch: 0 },
      controller: false,
      layers: [],
    });
    deckRef.current = deck;

    map.on('move', () => {
      const center = map.getCenter();
      deck.setProps({
        viewState: {
          latitude: center.lat,
          longitude: center.lng,
          zoom: map.getZoom(),
          bearing: map.getBearing(),
          pitch: map.getPitch(),
        },
      });
    });

    const loop = (ts: number) => {
      const dt = lastTsRef.current ? Math.min(200, ts - lastTsRef.current) : 16;
      lastTsRef.current = ts;
      const activeNetwork = networkRef.current;
      const engine = engineRef.current;
      if (activeNetwork && engine && deckRef.current) {
        const { vehicles, signals } = playingRef.current
          ? engine.tick(dt)
          : engine.tick(0);
        const zoom = mapRef.current?.getZoom() ?? 17.2;
        deckRef.current.setProps({ layers: buildSceneLayers(activeNetwork, vehicles, signals, zoom) });
        if (playingRef.current && ts - lastPublishRef.current >= 500 && !publishingRef.current) {
          lastPublishRef.current = ts;
          publishingRef.current = true;
          void publishFrame(vehicles, signals)
            .then(() => setFeedState('live'))
            .catch(() => setFeedState('offline'))
            .finally(() => { publishingRef.current = false; });
        }
      }
      animRef.current = requestAnimationFrame(loop);
    };
    animRef.current = requestAnimationFrame(loop);

    return () => {
      cancelAnimationFrame(animRef.current);
      deck.finalize();
      map.remove();
    };
    // Scene is intentionally initialized once; junction/vehicle/chaos changes are applied imperatively below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const changeVehicleCount = (count: number) => {
    setVehicleCount(count);
    engineRef.current?.setVehicleCount(count);
  };

  const changeChaos = (value: number) => {
    setChaos(value);
    engineRef.current?.setChaos(value);
  };

  const reset = resetNetwork;

  return (
    <div style={s.container}>
      <header style={s.header}>
        <Link to="/" style={s.backLink}><ArrowLeft size={18} /> Control Center</Link>
        <div style={s.headerCenter}>
          <TrainTrack size={18} style={{ color: 'var(--accent-purple)' }} />
          <span style={{ fontWeight: 700, fontSize: 15 }}>Connected Road Network</span>
        </div>
        <div style={s.feedBadge}><Radio size={13} /> {feedState === 'live' ? 'Gateway feed live' : feedState === 'offline' ? 'Gateway unavailable' : 'Connecting gateway'}</div>
      </header>

      <div style={s.main}>
        <aside style={s.sidebar}>
          <div style={s.field}>
            <label style={s.label}>Connected junction district</label>
            <div style={s.topology}>
              <span><CheckCircle2 size={13} /> Signalised cross hub</span>
              <span><CheckCircle2 size={13} /> Roundabout branch</span>
              <span><CheckCircle2 size={13} /> Railway crossing branch</span>
              <span><CheckCircle2 size={13} /> T-junction branch</span>
            </div>
            <p style={s.description}>One road network, with each branch joined to the central hub. Signal phases and gate state are sent to the gateway with every live frame.</p>
          </div>

          <div style={s.field}>
            <label style={s.label}>Vehicles: {vehicleCount}</label>
            <input
              type="range" min={4} max={60} value={vehicleCount}
              onChange={(e) => changeVehicleCount(parseInt(e.target.value, 10))}
              style={s.slider}
            />
          </div>

          <div style={s.field}>
            <label style={s.label}>Chaos (unpredictability): {Math.round(chaos * 100)}%</label>
            <input
              type="range" min={0} max={100} value={Math.round(chaos * 100)}
              onChange={(e) => changeChaos(parseInt(e.target.value, 10) / 100)}
              style={s.slider}
            />
            <p style={s.hint}>Higher chaos means more sudden bursts of speed and hard braking — cars and scooters behave less predictably, not uniformly.</p>
          </div>

          <div style={{ display: 'flex', gap: 8 }}>
            <button onClick={() => setPlaying((p) => !p)} style={s.actionBtn}>
              {playing ? <Pause size={14} /> : <Play size={14} />} {playing ? 'Pause' : 'Play'}
            </button>
            <button onClick={reset} style={s.actionBtn}><RotateCcw size={14} /> Reset</button>
          </div>

          <p style={s.footnote}>
            This simulator is a canonical telemetry adapter. Its vehicles and signals are published to the gateway, then appear in the Control Center through WorldStream.
          </p>
        </aside>

        <div style={s.mapArea}>
          <div ref={mapContainer} style={{ width: '100%', height: '100%' }} />
        </div>
      </div>
    </div>
  );
}

async function publishFrame(vehicles: VehicleState[], signals: TrafficSignalState[]): Promise<void> {
  const events = vehicles.map((vehicle) => ({
    event_type: 'actor.state.updated', timestamp_utc: vehicle.ts, source: 'junction-network',
    payload: { ...vehicle, vehicle_id: vehicle.actor_id, vehicle_type: ACTOR_TYPE_FOR_ADAPTER[vehicle.actor_type] },
  }));
  const telemetry = await fetch('/v1/world-state/ingest', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ events }),
  });
  if (!telemetry.ok) throw new Error(`vehicle ingest failed (${telemetry.status})`);
  const responses = await Promise.all(signals.map((signal) => {
    const movements = Object.fromEntries((signal.phases ?? []).map((phase) => [phase.movement_id, phase.state]));
    const currentPhase = signal.phases?.find((phase) => phase.state === 'GREEN')?.movement_id
      ?? signal.phases?.[0]?.state
      ?? 'RED';
    // Normalize the display-friendly signal shape to the gateway's canonical
    // TrafficSignalState contract at the adapter boundary.
    const canonicalSignal = {
      schema_version: '1.0', signal_id: signal.signal_id,
      intersection_id: signal.intersection_id ?? signal.junction_id ?? 'junction-network',
      ts: signal.ts, position: signal.position, current_phase: currentPhase,
      movements, source: signal.source,
    };
    return fetch('/v1/ingest/signal-state', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(canonicalSignal),
    });
  }));
  if (responses.some((response) => !response.ok)) throw new Error('signal ingest failed');
}

function buildSceneLayers(network: JunctionNetwork, vehicles: VehicleState[], signals: TrafficSignalState[], zoom: number) {
  const roads = network.junctions.flatMap((junction) => junction.roads);
  const laneMarkings = network.junctions.flatMap((junction) => junction.laneMarkings);
  const areaPolygons = network.junctions.flatMap((junction) => junction.areaPolygons);
  const rails = network.junctions.flatMap((junction) => junction.rails ?? []);
  const sleepers = network.junctions.flatMap((junction) => junction.sleepers ?? []);
  return [
    new PathLayer({
      id: 'sim-road-bed', data: roads, getPath: (d: Point[]) => d,
      getColor: BED_COLOR, getWidth: 13, widthUnits: 'meters',
      capRounded: true, jointRounded: true, pickable: false,
    }),
    new PathLayer({
      id: 'sim-road-surface', data: roads, getPath: (d: Point[]) => d,
      getColor: SURFACE_COLOR, getWidth: 10, widthUnits: 'meters',
      capRounded: true, jointRounded: true, pickable: false,
    }),
    ...(laneMarkings.length > 0 ? [new PathLayer({
      id: 'sim-lane-marks', data: laneMarkings, getPath: (d: Point[]) => d,
      getColor: LANE_LINE_COLOR, getWidth: 0.6, widthUnits: 'meters', pickable: false,
    })] : []),
    new PolygonLayer({
      id: 'sim-area', data: areaPolygons,
      getPolygon: (d: { polygon: Point[] }) => d.polygon,
      getFillColor: (d: { fill: [number, number, number, number] }) => d.fill,
      getLineColor: (d: { line: [number, number, number, number] }) => d.line,
      getLineWidth: 1, lineWidthUnits: 'pixels', stroked: true, filled: true, pickable: false,
    }),
    ...(rails.length ? [new PathLayer({
      id: 'sim-rails', data: rails, getPath: (d: Point[]) => d,
      getColor: RAIL_TRACK_COLOR, getWidth: 0.18, widthUnits: 'meters', pickable: false,
    })] : []),
    ...(sleepers.length ? [new PathLayer({
      id: 'sim-sleepers', data: sleepers, getPath: (d: Point[]) => d,
      getColor: SLEEPER_TIE_COLOR, getWidth: 0.35, widthUnits: 'meters', pickable: false,
    })] : []),
    ...createActorLayer(vehicles, [], [], zoom, false, null),
    ...createInfrastructureLayer(signals, [], [], zoom),
  ];
}

const s: Record<string, React.CSSProperties> = {
  container: { display: 'flex', flexDirection: 'column', height: '100vh', overflow: 'hidden', background: 'var(--bg-primary)' },
  header: {
    display: 'flex', alignItems: 'center', height: 48, padding: '0 16px',
    background: 'var(--bg-secondary)', borderBottom: '1px solid var(--border-primary)', flexShrink: 0,
  },
  backLink: { display: 'flex', alignItems: 'center', gap: 6, color: 'var(--text-secondary)', textDecoration: 'none', fontSize: 13, fontWeight: 500, width: 140 },
  headerCenter: { display: 'flex', alignItems: 'center', gap: 8, margin: '0 auto' },
  feedBadge: { width: 180, display: 'flex', justifyContent: 'flex-end', alignItems: 'center', gap: 6, color: 'var(--accent-green)', fontSize: 11, fontWeight: 650 },
  main: { flex: 1, display: 'flex', overflow: 'hidden' },
  sidebar: { width: 320, flexShrink: 0, overflow: 'auto', padding: 16, background: 'var(--bg-secondary)', borderRight: '1px solid var(--border-primary)', display: 'flex', flexDirection: 'column', gap: 18 },
  field: { display: 'flex', flexDirection: 'column', gap: 8 },
  label: { fontSize: 11, fontWeight: 600, color: 'var(--text-secondary)', textTransform: 'uppercase', letterSpacing: 0.5 },
  topology: { display: 'grid', gap: 7, padding: 11, borderRadius: 'var(--radius-sm)', border: '1px solid var(--border-primary)', background: 'var(--bg-primary)', color: 'var(--text-secondary)', fontSize: 11 },
  description: { fontSize: 11, lineHeight: 1.5, color: 'var(--text-muted)', margin: 0 },
  slider: { width: '100%' },
  hint: { fontSize: 11, lineHeight: 1.5, color: 'var(--text-muted)', margin: 0 },
  actionBtn: { flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6, padding: '8px 10px', background: 'var(--bg-tertiary)', border: '1px solid var(--border-primary)', borderRadius: 'var(--radius-md)', color: 'var(--text-primary)', fontSize: 12, fontWeight: 600, cursor: 'pointer' },
  footnote: { fontSize: 10.5, lineHeight: 1.5, color: 'var(--text-muted)', marginTop: 'auto', paddingTop: 12, borderTop: '1px solid var(--border-primary)' },
  mapArea: { flex: 1, position: 'relative' },
};
