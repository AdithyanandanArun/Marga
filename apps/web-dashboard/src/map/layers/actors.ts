import { ScatterplotLayer } from '@deck.gl/layers';
import type { VehicleState, PedestrianState, DynamicActorObservation } from '../../types/canonical';

const ACTOR_COLORS: Record<string, [number, number, number]> = {
  CAR: [74, 125, 255],
  BIKE: [34, 211, 238],
  AUTO: [251, 191, 36],
  BUS: [167, 139, 250],
  TRUCK: [251, 146, 60],
  AMBULANCE: [248, 113, 113],
  OTHER: [155, 161, 176],
};

const PEDESTRIAN_COLOR: [number, number, number] = [52, 211, 153];
const ANIMAL_COLOR: [number, number, number] = [251, 191, 36];
const SELECTED_COLOR: [number, number, number] = [255, 255, 255];

export function createActorLayer(
  vehicles: VehicleState[],
  pedestrians: PedestrianState[],
  dynamicActors: DynamicActorObservation[],
  zoom: number,
  showUncertainty: boolean,
  selectedId: string | null,
) {
  const layers = [];

  if (showUncertainty && zoom > 14) {
    layers.push(
      new ScatterplotLayer({
        id: 'vehicle-uncertainty',
        data: vehicles,
        getPosition: (d: VehicleState) => [d.position.lon, d.position.lat],
        getRadius: (d: VehicleState) => d.position_uncertainty_m,
        getFillColor: [74, 125, 255, 30],
        getLineColor: [74, 125, 255, 60],
        lineWidthMinPixels: 1,
        stroked: true,
        filled: true,
        pickable: false,
        radiusUnits: 'meters',
      }),
    );
  }

  const vehicleRadius = zoom > 16 ? 6 : zoom > 14 ? 5 : zoom > 12 ? 4 : 3;

  layers.push(
    new ScatterplotLayer({
      id: 'vehicles',
      data: vehicles,
      getPosition: (d: VehicleState) => [d.position.lon, d.position.lat],
      getRadius: (d: VehicleState) => d.actor_id === selectedId ? vehicleRadius + 2 : vehicleRadius,
      getFillColor: (d: VehicleState) =>
        d.actor_id === selectedId ? SELECTED_COLOR : (ACTOR_COLORS[d.actor_type] ?? ACTOR_COLORS.OTHER),
      getLineColor: (d: VehicleState) =>
        d.lifecycle === 'DEGRADED' ? [251, 191, 36, 200] as [number, number, number, number]
        : d.lifecycle === 'STALE' ? [155, 161, 176, 150] as [number, number, number, number]
        : [0, 0, 0, 0] as [number, number, number, number],
      lineWidthMinPixels: 1,
      stroked: true,
      filled: true,
      pickable: true,
      radiusUnits: 'pixels',
      updateTriggers: {
        getRadius: selectedId,
        getFillColor: selectedId,
      },
    }),
  );

  if (zoom > 13) {
    layers.push(
      new ScatterplotLayer({
        id: 'pedestrians',
        data: pedestrians,
        getPosition: (d: PedestrianState) => [d.position.lon, d.position.lat],
        getRadius: zoom > 15 ? 5 : 3,
        getFillColor: PEDESTRIAN_COLOR,
        pickable: true,
        radiusUnits: 'pixels',
      }),
    );
  }

  if (zoom > 13 && dynamicActors.length > 0) {
    if (showUncertainty) {
      layers.push(
        new ScatterplotLayer({
          id: 'dynamic-actor-uncertainty',
          data: dynamicActors,
          getPosition: (d: DynamicActorObservation) => [d.position.lon, d.position.lat],
          getRadius: (d: DynamicActorObservation) => d.position_uncertainty_m,
          getFillColor: [251, 191, 36, 25],
          getLineColor: [251, 191, 36, 50],
          lineWidthMinPixels: 1,
          stroked: true,
          filled: true,
          pickable: false,
          radiusUnits: 'meters',
        }),
      );
    }

    layers.push(
      new ScatterplotLayer({
        id: 'dynamic-actors',
        data: dynamicActors,
        getPosition: (d: DynamicActorObservation) => [d.position.lon, d.position.lat],
        getRadius: zoom > 15 ? 7 : 5,
        getFillColor: ANIMAL_COLOR,
        getLineColor: [0, 0, 0, 100],
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
