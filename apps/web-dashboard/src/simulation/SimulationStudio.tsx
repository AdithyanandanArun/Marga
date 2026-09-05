import { useCallback, useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import maplibregl from 'maplibre-gl';
import { Deck } from '@deck.gl/core';
import { PathLayer, PolygonLayer } from '@deck.gl/layers';
import { ArrowLeft, Pause, Play, RotateCcw, TrainTrack } from 'lucide-react';
import type { VehicleState, TrafficSignalState } from '../types/canonical';
import { createActorLayer } from '../map/layers/actors';
import { createInfrastructureLayer } from '../map/layers/infrastructure';
import {
  buildJunction,
  JUNCTION_TYPES,
  BED_COLOR,
  SURFACE_COLOR,
  LANE_LINE_COLOR,
  RAIL_TRACK_COLOR,
  SLEEPER_TIE_COLOR,
  type JunctionDefinition,
  type JunctionType,
  type Point,
} from './junctionDefs';
import { JunctionSimEngine } from './vehicleEngine';

// A fixed local coordinate frame with no relation to any real place — this
// view never touches worldStore or WorldStream, so it can never affect the
// gateway-connected Dashboard or the backend it depends on.
const SIM_LAT = 12.9550;
const SIM_LON = 77.6200;

const SIM_STYLE: maplibregl.StyleSpecification = {
  version: 8,
  sources: {},
  layers: [{ id: 'sim-bg', type: 'background', paint: { 'background-color': '#0d1017' } }],
};

const TYPE_LABEL: Record<JunctionType, string> = {
  CROSS: 'Cross',
  T_JUNCTION: 'T-junction',
  ROUNDABOUT: 'Roundabout',
  RAILWAY_CROSSING: 'Rail crossing',
};

export function SimulationStudio() {
  const mapContainer = useRef<HTMLDivElement>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const deckRef = useRef<Deck | null>(null);
  const engineRef = useRef<JunctionSimEngine | null>(null);
  const junctionRef = useRef<JunctionDefinition | null>(null);
  const animRef = useRef<number>(0);
  const lastTsRef = useRef<number>(0);
  const playingRef = useRef(true);

  const [junctionType, setJunctionType] = useState<JunctionType>('CROSS');
  const [vehicleCount, setVehicleCount] = useState(24);
  const [chaos, setChaos] = useState(0.5);
  const [playing, setPlaying] = useState(true);

  useEffect(() => { playingRef.current = playing; }, [playing]);

  const rebuildScene = useCallback((type: JunctionType) => {
    const def = buildJunction(type, SIM_LAT, SIM_LON);
    junctionRef.current = def;
    if (engineRef.current) {
      engineRef.current.setJunction(def, vehicleCount);
    } else {
      engineRef.current = new JunctionSimEngine({ junction: def, vehicleCount, chaos });
    }
    mapRef.current?.flyTo({ center: def.center, zoom: 17.2, duration: 700, essential: true });
  }, [vehicleCount, chaos]);

  useEffect(() => {
    if (!mapContainer.current) return;

    const def = buildJunction(junctionType, SIM_LAT, SIM_LON);
    junctionRef.current = def;
    engineRef.current = new JunctionSimEngine({ junction: def, vehicleCount, chaos });

    const map = new maplibregl.Map({
      container: mapContainer.current,
      style: SIM_STYLE,
      center: def.center,
      zoom: 17.2,
      antialias: true,
    });
    map.addControl(new maplibregl.NavigationControl(), 'top-right');
    mapRef.current = map;

    const deck = new Deck({
      parent: mapContainer.current,
      style: { position: 'absolute', top: '0', left: '0', zIndex: '1', pointerEvents: 'none' },
      viewState: { latitude: def.center[1], longitude: def.center[0], zoom: 17.2, bearing: 0, pitch: 0 },
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
      const junction = junctionRef.current;
      const engine = engineRef.current;
      if (junction && engine && deckRef.current) {
        const { vehicles, signals } = playingRef.current
          ? engine.tick(dt)
          : engine.tick(0);
        const zoom = mapRef.current?.getZoom() ?? 17.2;
        deckRef.current.setProps({ layers: buildSceneLayers(junction, vehicles, signals, zoom) });
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

  const changeJunctionType = (type: JunctionType) => {
    setJunctionType(type);
    rebuildScene(type);
  };

  const changeVehicleCount = (count: number) => {
    setVehicleCount(count);
    engineRef.current?.setVehicleCount(count);
  };

  const changeChaos = (value: number) => {
    setChaos(value);
    engineRef.current?.setChaos(value);
  };

  const reset = () => rebuildScene(junctionType);

  const def = junctionRef.current;

  return (
    <div style={s.container}>
      <header style={s.header}>
        <Link to="/" style={s.backLink}><ArrowLeft size={18} /> Control Center</Link>
        <div style={s.headerCenter}>
          <TrainTrack size={18} style={{ color: 'var(--accent-purple)' }} />
          <span style={{ fontWeight: 700, fontSize: 15 }}>Junction Simulator</span>
        </div>
        <div style={{ width: 140 }} />
      </header>

      <div style={s.main}>
        <aside style={s.sidebar}>
          <div style={s.field}>
            <label style={s.label}>Junction type</label>
            <div style={s.typeGrid}>
              {JUNCTION_TYPES.map((t) => (
                <button
                  key={t}
                  onClick={() => changeJunctionType(t)}
                  style={{ ...s.typeBtn, ...(junctionType === t ? s.typeBtnActive : {}) }}
                >
                  {TYPE_LABEL[t]}
                </button>
              ))}
            </div>
            {def && <p style={s.description}>{def.description}</p>}
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
            Client-side only. This simulation never reads from or writes to the gateway, WorldStream, or worldStore — it cannot affect live telemetry or the backend.
          </p>
        </aside>

        <div style={s.mapArea}>
          <div ref={mapContainer} style={{ width: '100%', height: '100%' }} />
        </div>
      </div>
    </div>
  );
}

function buildSceneLayers(def: JunctionDefinition, vehicles: VehicleState[], signals: TrafficSignalState[], zoom: number) {
  return [
    new PathLayer({
      id: 'sim-road-bed', data: def.roads, getPath: (d: Point[]) => d,
      getColor: BED_COLOR, getWidth: 13, widthUnits: 'meters',
      capRounded: true, jointRounded: true, pickable: false,
    }),
    new PathLayer({
      id: 'sim-road-surface', data: def.roads, getPath: (d: Point[]) => d,
      getColor: SURFACE_COLOR, getWidth: 10, widthUnits: 'meters',
      capRounded: true, jointRounded: true, pickable: false,
    }),
    ...(def.laneMarkings.length > 0 ? [new PathLayer({
      id: 'sim-lane-marks', data: def.laneMarkings, getPath: (d: Point[]) => d,
      getColor: LANE_LINE_COLOR, getWidth: 0.6, widthUnits: 'meters', pickable: false,
    })] : []),
    new PolygonLayer({
      id: 'sim-area', data: def.areaPolygons,
      getPolygon: (d: { polygon: Point[] }) => d.polygon,
      getFillColor: (d: { fill: [number, number, number, number] }) => d.fill,
      getLineColor: (d: { line: [number, number, number, number] }) => d.line,
      getLineWidth: 1, lineWidthUnits: 'pixels', stroked: true, filled: true, pickable: false,
    }),
    ...(def.rails ? [new PathLayer({
      id: 'sim-rails', data: def.rails, getPath: (d: Point[]) => d,
      getColor: RAIL_TRACK_COLOR, getWidth: 0.18, widthUnits: 'meters', pickable: false,
    })] : []),
    ...(def.sleepers ? [new PathLayer({
      id: 'sim-sleepers', data: def.sleepers, getPath: (d: Point[]) => d,
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
  main: { flex: 1, display: 'flex', overflow: 'hidden' },
  sidebar: { width: 320, flexShrink: 0, overflow: 'auto', padding: 16, background: 'var(--bg-secondary)', borderRight: '1px solid var(--border-primary)', display: 'flex', flexDirection: 'column', gap: 18 },
  field: { display: 'flex', flexDirection: 'column', gap: 8 },
  label: { fontSize: 11, fontWeight: 600, color: 'var(--text-secondary)', textTransform: 'uppercase', letterSpacing: 0.5 },
  typeGrid: { display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 6 },
  typeBtn: { padding: '8px 6px', border: '1px solid var(--border-primary)', borderRadius: 'var(--radius-sm)', background: 'var(--bg-primary)', color: 'var(--text-secondary)', fontSize: 12, fontWeight: 500, cursor: 'pointer' },
  typeBtnActive: { background: 'var(--accent-purple)', borderColor: 'var(--accent-purple)', color: '#fff' },
  description: { fontSize: 11, lineHeight: 1.5, color: 'var(--text-muted)', margin: 0 },
  slider: { width: '100%' },
  hint: { fontSize: 11, lineHeight: 1.5, color: 'var(--text-muted)', margin: 0 },
  actionBtn: { flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6, padding: '8px 10px', background: 'var(--bg-tertiary)', border: '1px solid var(--border-primary)', borderRadius: 'var(--radius-md)', color: 'var(--text-primary)', fontSize: 12, fontWeight: 600, cursor: 'pointer' },
  footnote: { fontSize: 10.5, lineHeight: 1.5, color: 'var(--text-muted)', marginTop: 'auto', paddingTop: 12, borderTop: '1px solid var(--border-primary)' },
  mapArea: { flex: 1, position: 'relative' },
};
