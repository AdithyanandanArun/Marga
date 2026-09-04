import { ScatterplotLayer, GeoJsonLayer } from '@deck.gl/layers';
import type { TrafficSignalState, RoadEvent, RSUState } from '../../types/canonical';

const SIGNAL_COLORS: Record<string, [number, number, number]> = {
  RED: [239, 68, 68],
  AMBER: [234, 179, 8],
  GREEN: [34, 197, 94],
};

const RSU_COLOR: [number, number, number] = [167, 139, 250];

const ROAD_EVENT_COLORS: Record<string, [number, number, number]> = {
  LANE_NARROWING: [251, 146, 60],
  LANE_CLOSURE: [239, 68, 68],
  ROAD_CLOSURE: [239, 68, 68],
  CONSTRUCTION: [251, 191, 36],
};

export function createInfrastructureLayer(
  signals: TrafficSignalState[],
  roadEvents: RoadEvent[],
  rsus: RSUState[],
  zoom: number,
) {
  const layers = [];

  if (signals.length > 0 && zoom > 13) {
    layers.push(
      new ScatterplotLayer({
        id: 'signals',
        data: signals,
        getPosition: () => [0, 0],
        getRadius: 0,
        getFillColor: [0, 0, 0, 0],
        pickable: false,
        radiusUnits: 'pixels',
      }),
    );

    const signalPoints = signals.flatMap((s) =>
      s.phases.map((p) => ({
        signal_id: s.signal_id,
        junction_id: s.junction_id,
        position: [0, 0] as [number, number],
        color: SIGNAL_COLORS[p.state] ?? SIGNAL_COLORS.RED,
        phase_state: p.state,
      })),
    );

    if (signalPoints.length > 0) {
      layers.push(
        new ScatterplotLayer({
          id: 'signal-indicators',
          data: signals,
          getPosition: () => [77.5946 + Math.random() * 0.03 - 0.015, 12.9716 + Math.random() * 0.02 - 0.01] as [number, number],
          getRadius: zoom > 15 ? 8 : 6,
          getFillColor: (d: TrafficSignalState) => {
            const primary = d.phases[0]?.state ?? 'RED';
            return SIGNAL_COLORS[primary] ?? SIGNAL_COLORS.RED;
          },
          getLineColor: [255, 255, 255, 100],
          lineWidthMinPixels: 1,
          stroked: true,
          filled: true,
          pickable: true,
          radiusUnits: 'pixels',
        }),
      );
    }
  }

  if (roadEvents.length > 0 && zoom > 12) {
    const geojson: GeoJSON.FeatureCollection = {
      type: 'FeatureCollection',
      features: roadEvents.map((e) => ({
        type: 'Feature' as const,
        geometry: e.geometry,
        properties: {
          event_id: e.event_id,
          type: e.type,
          severity: e.severity,
        },
      })),
    };

    layers.push(
      new GeoJsonLayer({
        id: 'road-events',
        data: geojson,
        getLineColor: (f: GeoJSON.Feature) => {
          const type = f.properties?.type as string;
          const color = ROAD_EVENT_COLORS[type] ?? [155, 161, 176];
          return [...color, 200] as [number, number, number, number];
        },
        getFillColor: (f: GeoJSON.Feature) => {
          const type = f.properties?.type as string;
          const color = ROAD_EVENT_COLORS[type] ?? [155, 161, 176];
          return [...color, 40] as [number, number, number, number];
        },
        getLineWidth: 4,
        lineWidthUnits: 'pixels',
        pickable: true,
      }),
    );
  }

  if (rsus.length > 0 && zoom > 12) {
    layers.push(
      new ScatterplotLayer({
        id: 'rsu-coverage',
        data: rsus,
        getPosition: (d: RSUState) => [d.position.lon, d.position.lat],
        getRadius: (d: RSUState) => d.coverage_m,
        getFillColor: [...RSU_COLOR, 15] as [number, number, number, number],
        getLineColor: [...RSU_COLOR, 50] as [number, number, number, number],
        lineWidthMinPixels: 1,
        stroked: true,
        filled: true,
        pickable: false,
        radiusUnits: 'meters',
      }),
      new ScatterplotLayer({
        id: 'rsu-nodes',
        data: rsus,
        getPosition: (d: RSUState) => [d.position.lon, d.position.lat],
        getRadius: zoom > 15 ? 8 : 6,
        getFillColor: RSU_COLOR,
        getLineColor: [255, 255, 255, 100],
        lineWidthMinPixels: 1,
        stroked: true,
        filled: true,
        pickable: true,
        radiusUnits: 'pixels',
      }),
    );
  }

  return layers;
}
