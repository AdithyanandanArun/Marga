import { useCallback, useEffect, useRef, useState } from 'react';
import maplibregl from 'maplibre-gl';
import { Deck } from '@deck.gl/core';
import { useUIStore } from '../state/uiStore';
import { useWorldStore } from '../state/worldStore';
import { createActorLayer } from './layers/actors';
import { createHazardLayer } from './layers/hazards';
import { createRiskLayer } from './layers/risks';
import { createInfrastructureLayer } from './layers/infrastructure';
import { createTrajectoriesLayer } from './layers/trajectories';
import { createV2XLinksLayer } from './layers/v2xLinks';
import { createJunctionRoadLayers } from './layers/junctionScene';
import type { VehicleState } from '../types/canonical';
import { selectPrimaryRisk } from '../utils/risk';

const DARK_STYLE = 'https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json';
const LIGHT_STYLE = 'https://basemaps.cartocdn.com/gl/positron-gl-style/style.json';

interface MapViewProps {
  onEntityClick?: (entityId: string, entityType: string) => void;
  onMapClick?: (lat: number, lon: number) => void;
  placementMode?: boolean;
  roadScene?: boolean;
}

export function MapView({ onEntityClick, onMapClick, placementMode = false, roadScene = false }: MapViewProps) {
  const mapContainer = useRef<HTMLDivElement>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const deckRef = useRef<Deck | null>(null);
  const animRef = useRef<number>(0);
  const hasAutoFramedRef = useRef(false);
  const onMapClickRef = useRef(onMapClick);
  const [mapLoaded, setMapLoaded] = useState(false);

  useEffect(() => { onMapClickRef.current = onMapClick; }, [onMapClick]);

  const viewport = useUIStore((s) => s.viewport);
  const darkMapStyle = useUIStore((s) => s.darkMapStyle);
  const setViewport = useUIStore((s) => s.setViewport);

  const vehicles = useWorldStore((s) => s.vehicles);
  const pedestrians = useWorldStore((s) => s.pedestrians);
  const hazards = useWorldStore((s) => s.hazards);
  const signals = useWorldStore((s) => s.signals);
  const roadEvents = useWorldStore((s) => s.roadEvents);
  const dynamicActors = useWorldStore((s) => s.dynamicActors);
  const rsus = useWorldStore((s) => s.rsus);
  const risks = useWorldStore((s) => s.risks);

  const showUncertainty = useUIStore((s) => s.showUncertainty);
  const showTrajectories = useUIStore((s) => s.showTrajectories);
  const showRiskZones = useUIStore((s) => s.showRiskZones);
  const showSignals = useUIStore((s) => s.showSignals);
  const showHazards = useUIStore((s) => s.showHazards);
  const showRoadEvents = useUIStore((s) => s.showRoadEvents);
  const showRSUs = useUIStore((s) => s.showRSUs);
  const showV2XLinks = useUIStore((s) => s.showV2XLinks);

  const selectedEntityId = useUIStore((s) => s.selectedEntityId);

  // The demo opens on the live road scene, rather than a city-scale view where
  // the mixed traffic and conflict are indistinguishable. This happens once;
  // subsequent pan/zoom is always controlled by the judge/operator.
  useEffect(() => {
    if (hasAutoFramedRef.current || !mapLoaded || !mapRef.current || vehicles.size === 0) return;
    const focusIds = selectPrimaryRisk(risks.values())?.affected_actor_ids;
    const actors = focusIds
      ? focusIds.map((id) => vehicles.get(id)).filter((actor): actor is VehicleState => actor !== undefined)
      : Array.from(vehicles.values());
    if (actors.length === 0) return;
    hasAutoFramedRef.current = true;
    const latitude = actors.reduce((sum, actor) => sum + actor.position.lat, 0) / actors.length;
    const longitude = actors.reduce((sum, actor) => sum + actor.position.lon, 0) / actors.length;
    mapRef.current.flyTo({ center: [longitude, latitude], zoom: 16, duration: 900, essential: true });
  }, [mapLoaded, vehicles, risks]);

  useEffect(() => {
    if (!mapContainer.current) return;

    const map = new maplibregl.Map({
      container: mapContainer.current,
      style: darkMapStyle ? DARK_STYLE : LIGHT_STYLE,
      center: [viewport.longitude, viewport.latitude],
      zoom: viewport.zoom,
      bearing: viewport.bearing,
      pitch: viewport.pitch,
      antialias: true,
    });

    map.addControl(new maplibregl.NavigationControl(), 'top-right');
    map.addControl(new maplibregl.ScaleControl({ unit: 'metric' }), 'bottom-right');

    map.on('load', () => {
      setMapLoaded(true);
    });

    map.on('click', (e) => {
      onMapClickRef.current?.(e.lngLat.lat, e.lngLat.lng);
    });

    map.on('moveend', () => {
      const center = map.getCenter();
      setViewport({
        latitude: center.lat,
        longitude: center.lng,
        zoom: map.getZoom(),
        bearing: map.getBearing(),
        pitch: map.getPitch(),
      });
    });

    mapRef.current = map;

    const deck = new Deck({
      parent: mapContainer.current,
      style: { position: 'absolute', top: '0', left: '0', zIndex: '1', pointerEvents: 'none' },
      viewState: {
        latitude: viewport.latitude,
        longitude: viewport.longitude,
        zoom: viewport.zoom,
        bearing: viewport.bearing,
        pitch: viewport.pitch,
      },
      controller: false,
      layers: [],
      getTooltip: ({ object }: { object?: Record<string, unknown> }) => {
        if (!object) return null;
        const id = (object as { actor_id?: string; hazard_id?: string; signal_id?: string }).actor_id
          || (object as { hazard_id?: string }).hazard_id
          || (object as { signal_id?: string }).signal_id
          || '';
        return id ? { text: id } : null;
      },
      onClick: (info: { object?: Record<string, unknown> }) => {
        if (!info.object) return;
        const obj = info.object as Record<string, string>;
        const id = obj.actor_id || obj.hazard_id || obj.signal_id || obj.rsu_id || obj.event_id || obj.observation_id || '';
        const type = obj.actor_id ? 'vehicle' : obj.hazard_id ? 'hazard' : obj.signal_id ? 'signal' : obj.rsu_id ? 'rsu' : 'unknown';
        if (id && onEntityClick) onEntityClick(id, type);
      },
    });

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

    deckRef.current = deck;

    return () => {
      cancelAnimationFrame(animRef.current);
      deck.finalize();
      map.remove();
    };
  }, []);

  const updateLayers = useCallback(() => {
    if (!deckRef.current) return;

    const zoom = mapRef.current?.getZoom() ?? viewport.zoom;
    const layers = [
      ...(roadScene ? createJunctionRoadLayers() : []),
      ...createActorLayer(
        Array.from(vehicles.values()),
        Array.from(pedestrians.values()),
        Array.from(dynamicActors.values()),
        zoom,
        showUncertainty,
        selectedEntityId,
      ),
      ...(showHazards ? createHazardLayer(Array.from(hazards.values()), zoom) : []),
      ...(showRiskZones ? createRiskLayer(Array.from(risks.values()), vehicles, zoom) : []),
      ...(showTrajectories ? [createTrajectoriesLayer(Array.from(vehicles.values()), Array.from(risks.values())[0]?.affected_actor_ids)] : []),
      ...(showV2XLinks ? [createV2XLinksLayer(Array.from(vehicles.values()), Array.from(rsus.values()))] : []),
      ...createInfrastructureLayer(
        showSignals ? Array.from(signals.values()) : [],
        showRoadEvents ? Array.from(roadEvents.values()) : [],
        showRSUs ? Array.from(rsus.values()) : [],
        zoom,
      ),
    ];

    deckRef.current.setProps({ layers });
  }, [vehicles, pedestrians, hazards, signals, roadEvents, dynamicActors, rsus, risks,
      showUncertainty, showTrajectories, showRiskZones, showV2XLinks, showSignals, showHazards, showRoadEvents, showRSUs,
      selectedEntityId, viewport.zoom, roadScene]);

  useEffect(() => {
    const loop = () => {
      updateLayers();
      animRef.current = requestAnimationFrame(loop);
    };
    animRef.current = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(animRef.current);
  }, [updateLayers]);

  return (
    <div style={{ width: '100%', height: '100%', position: 'relative' }}>
      <div ref={mapContainer} style={{ width: '100%', height: '100%', cursor: placementMode ? 'crosshair' : undefined }} />
      {placementMode && (
        <div style={{
          position: 'absolute', top: 12, left: '50%', transform: 'translateX(-50%)',
          background: 'rgba(99,102,241,0.9)', color: '#fff', padding: '6px 16px',
          borderRadius: 20, fontSize: 12, fontWeight: 600, pointerEvents: 'none', zIndex: 10,
        }}>
          Click on map to place actor
        </div>
      )}
      {mapLoaded && vehicles.size === 0 && !placementMode && (
        <div style={{
          position: 'absolute', left: '50%', top: '50%', transform: 'translate(-50%, -50%)',
          width: 280, padding: 20, borderRadius: 12, textAlign: 'center',
          background: 'rgba(15, 17, 23, 0.9)', border: '1px solid rgba(255, 255, 255, 0.14)',
          color: 'var(--text-primary)', pointerEvents: 'none', zIndex: 2,
        }}>
          <div style={{ fontSize: 13, fontWeight: 700, letterSpacing: 0.8 }}>WAITING FOR ROAD TELEMETRY</div>
          <div style={{ marginTop: 8, fontSize: 12, lineHeight: 1.5, color: 'var(--text-secondary)' }}>
            Start a verified scenario or connect the gateway. Marga does not invent traffic or safety alerts.
          </div>
        </div>
      )}
    </div>
  );
}
