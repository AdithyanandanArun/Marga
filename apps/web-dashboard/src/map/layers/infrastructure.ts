import { ScatterplotLayer, GeoJsonLayer, TextLayer } from '@deck.gl/layers';
import type { TrafficSignalState, RoadEvent, RSUState } from '../../types/canonical';

const SIGNAL_COLORS: Record<string, [number, number, number]> = {
  RED: [239, 68, 68],
  AMBER: [245, 158, 11],
  GREEN: [34, 197, 94],
};

const RSU_COLOR: [number, number, number] = [167, 139, 250];

const ROAD_EVENT_COLORS: Record<string, [number, number, number]> = {
  LANE_NARROWING: [251, 146, 60],
  LANE_CLOSURE: [239, 68, 68],
  ROAD_CLOSURE: [239, 68, 68],
  CONSTRUCTION: [251, 191, 36],
};

function activePhase(signal: TrafficSignalState): string {
  if (signal.current_phase?.includes('GREEN')) return 'GREEN';
  if (signal.current_phase?.includes('AMBER')) return 'AMBER';
  if (signal.movements) return Object.values(signal.movements).includes('GREEN') ? 'GREEN' : 'RED';
  return signal.phases?.[0]?.state ?? 'RED';
}

export function createInfrastructureLayer(
  signals: TrafficSignalState[],
  roadEvents: RoadEvent[],
  rsus: RSUState[],
  zoom: number,
) {
  const layers = [];

  if (signals.length > 0 && zoom >= 12) {
    const signalsWithPos = signals;

    if (signalsWithPos.length > 0) {
      const dotRadius = zoom > 15 ? 12 : zoom > 13 ? 9 : 7;

      // Dark housing ring — makes it look like a mounted signal box
      layers.push(
        new ScatterplotLayer({
          id: 'signal-housing',
          data: signalsWithPos,
          getPosition: (d: TrafficSignalState) => [d.position.lon, d.position.lat],
          getRadius: dotRadius + 3,
          getFillColor: [30, 30, 30, 220],
          getLineColor: [80, 80, 80, 180],
          lineWidthMinPixels: 1,
          stroked: true,
          filled: true,
          pickable: false,
          radiusUnits: 'pixels',
        }),
      );

      // Colored lens — active phase color
      layers.push(
        new ScatterplotLayer({
          id: 'signal-lens',
          data: signalsWithPos,
          getPosition: (d: TrafficSignalState) => [d.position.lon, d.position.lat],
          getRadius: dotRadius,
          getFillColor: (d: TrafficSignalState) => {
            const phase = activePhase(d);
            return [...(SIGNAL_COLORS[phase] ?? SIGNAL_COLORS.RED), 255] as [number, number, number, number];
          },
          getLineColor: [255, 255, 255, 60],
          lineWidthMinPixels: 1,
          stroked: true,
          filled: true,
          pickable: true,
          radiusUnits: 'pixels',
          updateTriggers: {
            getFillColor: signalsWithPos.map(activePhase),
          },
        }),
      );

      // Phase letter label when zoomed well in
      if (zoom > 14) {
        layers.push(
          new TextLayer({
            id: 'signal-labels',
            data: signalsWithPos,
            getPosition: (d: TrafficSignalState) => [d.position.lon, d.position.lat],
            getText: (d: TrafficSignalState) => activePhase(d).charAt(0),
            getSize: 11,
            getColor: [255, 255, 255, 230],
            getTextAnchor: 'middle',
            getAlignmentBaseline: 'center',
            fontFamily: 'monospace',
            fontWeight: 'bold',
          }),
        );
      }
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
