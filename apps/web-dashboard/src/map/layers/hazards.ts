import { ScatterplotLayer, GeoJsonLayer } from '@deck.gl/layers';
import type { Hazard } from '../../types/canonical';

const HAZARD_COLORS: Record<string, [number, number, number]> = {
  POTHOLE: [251, 146, 60],
  BUMP: [234, 179, 8],
  DEBRIS: [155, 161, 176],
  FLOOD: [234, 179, 8],
  LANDSLIDE: [168, 85, 47],
  ANIMAL: [251, 191, 36],
  STALLED_VEHICLE: [248, 113, 113],
  CONSTRUCTION: [251, 146, 60],
  LANE_CLOSURE: [239, 68, 68],
  ACCIDENT: [239, 68, 68],
  LOW_VISIBILITY: [107, 114, 128],
  OTHER: [155, 161, 176],
};

export function createHazardLayer(hazards: Hazard[], zoom: number) {
  if (zoom < 11) return [];

  const pointHazards = hazards.filter(
    (h) => h.geometry.type === 'Point',
  );
  const geoHazards = hazards.filter(
    (h) => h.geometry.type !== 'Point',
  );

  const layers = [];

  if (pointHazards.length > 0) {
    layers.push(
      new ScatterplotLayer({
        id: 'hazard-points',
        data: pointHazards,
        getPosition: (d: Hazard) => {
          const coords = (d.geometry as GeoJSON.Point).coordinates;
          return [coords[0], coords[1]];
        },
        getRadius: (d: Hazard) => {
          const base = zoom > 15 ? 10 : zoom > 13 ? 8 : 6;
          return base * (0.5 + d.severity * 0.5);
        },
        getFillColor: (d: Hazard) => {
          const color = HAZARD_COLORS[d.type] ?? HAZARD_COLORS.OTHER;
          const alpha = Math.floor(100 + d.confidence * 155);
          return [...color, alpha] as [number, number, number, number];
        },
        getLineColor: (d: Hazard) => {
          const color = HAZARD_COLORS[d.type] ?? HAZARD_COLORS.OTHER;
          return [...color, 200] as [number, number, number, number];
        },
        lineWidthMinPixels: 2,
        stroked: true,
        filled: true,
        pickable: true,
        radiusUnits: 'pixels',
      }),
    );
  }

  if (geoHazards.length > 0) {
    const geojson: GeoJSON.FeatureCollection = {
      type: 'FeatureCollection',
      features: geoHazards.map((h) => ({
        type: 'Feature' as const,
        geometry: h.geometry,
        properties: {
          hazard_id: h.hazard_id,
          type: h.type,
          severity: h.severity,
          confidence: h.confidence,
          state: h.state,
        },
      })),
    };

    layers.push(
      new GeoJsonLayer({
        id: 'hazard-geo',
        data: geojson,
        getFillColor: (f: GeoJSON.Feature) => {
          const type = f.properties?.type as string;
          const color = HAZARD_COLORS[type] ?? HAZARD_COLORS.OTHER;
          return [...color, 60] as [number, number, number, number];
        },
        getLineColor: (f: GeoJSON.Feature) => {
          const type = f.properties?.type as string;
          const color = HAZARD_COLORS[type] ?? HAZARD_COLORS.OTHER;
          return [...color, 180] as [number, number, number, number];
        },
        getLineWidth: 3,
        lineWidthUnits: 'pixels',
        pickable: true,
      }),
    );
  }

  return layers;
}
