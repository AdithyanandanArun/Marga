import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import maplibregl from 'maplibre-gl';
import { Deck } from '@deck.gl/core';
import { PathLayer, PolygonLayer, TextLayer } from '@deck.gl/layers';
import { ArrowLeft, CheckCircle2, Pause, Play, Radio, RotateCcw, TrainTrack } from 'lucide-react';
import type { PedestrianState, VehicleState, TrafficSignalState } from '../types/canonical';
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
import { buildJunctionNetwork, type JunctionNetwork } from './networkEngine';
import { networkTelemetry, type NetworkFrame } from './networkTelemetry';
import { NETWORK_BOUNDS } from '../map/layers/networkScene';

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

export function SimulationStudio() {
  const mapContainer = useRef<HTMLDivElement>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const deckRef = useRef<Deck | null>(null);
  const networkRef = useRef<JunctionNetwork | null>(null);
  const lastRenderAt = useRef(0);

  const [vehicleCount, setVehicleCount] = useState(30);
  const [chaos, setChaos] = useState(0.65);
  // Seeded from the shared runtime rather than assumed: pausing here and
  // navigating away leaves the singleton paused, so a fresh `true` would show
  // a "Pause" button over a world that is standing still.
  const [playing, setPlaying] = useState(() => !networkTelemetry.isPaused());
  const [feedState, setFeedState] = useState<'connecting' | 'live' | 'offline'>('connecting');

  useEffect(() => {
    if (!mapContainer.current) return;

    const network = buildJunctionNetwork(SIM_LAT, SIM_LON);
    networkRef.current = network;

    const map = new maplibregl.Map({
      container: mapContainer.current,
      style: SIM_STYLE,
      center: network.center,
      zoom: 14.6,
      antialias: true,
    });
    map.on('load', () => map.fitBounds(NETWORK_BOUNDS, { padding: 70, duration: 0, maxZoom: 15.4 }));
    map.addControl(new maplibregl.NavigationControl(), 'top-right');
    mapRef.current = map;

    const deck = new Deck({
      parent: mapContainer.current,
      style: { position: 'absolute', top: '0', left: '0', zIndex: '1', pointerEvents: 'none' },
      viewState: { latitude: network.center[1], longitude: network.center[0], zoom: 14.6, bearing: 0, pitch: 0 },
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

    const onFrame = ({ vehicles, pedestrians, signals }: NetworkFrame) => {
      const activeNetwork = networkRef.current;
      if (!activeNetwork || !deckRef.current) return;
      const now = performance.now();
      if (now - lastRenderAt.current < 33) return;
      lastRenderAt.current = now;
      deckRef.current.setProps({ layers: buildSceneLayers(activeNetwork, vehicles, pedestrians, signals, mapRef.current?.getZoom() ?? 17.2) });
    };
    const releaseTelemetry = networkTelemetry.retain(onFrame, setFeedState);

    return () => {
      // Tearing down a WebGL context that has not finished initialising can
      // throw. If it escapes the cleanup, React abandons the rest of the
      // teardown and the remount never runs, so the map never appears and the
      // telemetry loop is left released and stopped.
      try { releaseTelemetry(); } catch { /* already released */ }
      try { deck.finalize(); } catch { /* context already lost */ }
      try { map.remove(); } catch { /* map already disposed */ }
    };
    // Scene is intentionally initialized once; junction/vehicle/chaos changes are applied imperatively below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const changeVehicleCount = (count: number) => {
    setVehicleCount(count);
    networkTelemetry.setVehicleCount(count);
  };

  const changeChaos = (value: number) => {
    setChaos(value);
    networkTelemetry.setChaos(value);
  };

  const reset = () => {
    networkTelemetry.reset(vehicleCount);
    const network = networkRef.current;
    if (network) mapRef.current?.flyTo({ center: network.center, zoom: 15.35, duration: 650, essential: true });
  };

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
            <p style={s.description}>One road network, with each branch joined to the central hub. The 30-vehicle mixed-traffic baseline deliberately exposes queues and predicted conflicts while preserving body-level collision avoidance. Signal phases and gate state are sent to the gateway with every live frame.</p>
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
            <button onClick={() => setPlaying((wasPlaying) => { networkTelemetry.setPaused(wasPlaying); return !wasPlaying; })} style={s.actionBtn}>
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

function buildSceneLayers(network: JunctionNetwork, vehicles: VehicleState[], pedestrians: PedestrianState[], signals: TrafficSignalState[], zoom: number) {
  const roads = network.junctions.flatMap((junction) => junction.roads);
  const laneMarkings = network.junctions.flatMap((junction) => junction.laneMarkings);
  const areaPolygons = network.junctions.flatMap((junction) => junction.areaPolygons);
  const rails = network.junctions.flatMap((junction) => junction.rails ?? []);
  const sleepers = network.junctions.flatMap((junction) => junction.sleepers ?? []);
  const crosswalks = network.junctions.flatMap((junction) => junction.crosswalks);
  const destinationRoads = network.destinationRoads;
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
    new PathLayer({ id: 'sim-zebra-crossings', data: crosswalks, getPath: (d: Point[]) => d, getColor: [245, 245, 245, 220], getWidth: 1.2, widthUnits: 'meters', pickable: false }),
    new PathLayer({ id: 'sim-destination-roads', data: destinationRoads, getPath: (d: typeof destinationRoads[number]) => d.path, getColor: (d: typeof destinationRoads[number]) => d.feature === 'CITY_RAIL' ? [245, 158, 11, 220] : d.feature === 'BUS_TERMINAL' ? [167, 139, 250, 220] : d.feature === 'AIRPORT_CORRIDOR' ? [34, 211, 238, 220] : [251, 146, 60, 220], getWidth: 8, widthUnits: 'meters', getDashArray: [12, 8], dashJustified: true, pickable: false }),
    new TextLayer({ id: 'sim-destination-labels', data: destinationRoads, getPosition: (d: typeof destinationRoads[number]) => d.path[d.path.length - 1], getText: (d: typeof destinationRoads[number]) => d.label, getSize: 13, sizeUnits: 'pixels', getColor: [226, 232, 240, 230], getBackgroundColor: [15, 23, 42, 210], background: true, getPixelOffset: [0, -12], billboard: true, pickable: false }),
    ...(rails.length ? [new PathLayer({
      id: 'sim-rails', data: rails, getPath: (d: Point[]) => d,
      getColor: RAIL_TRACK_COLOR, getWidth: 0.18, widthUnits: 'meters', pickable: false,
    })] : []),
    ...(sleepers.length ? [new PathLayer({
      id: 'sim-sleepers', data: sleepers, getPath: (d: Point[]) => d,
      getColor: SLEEPER_TIE_COLOR, getWidth: 0.35, widthUnits: 'meters', pickable: false,
    })] : []),
    ...createActorLayer(vehicles, pedestrians, [], zoom, false, null),
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
