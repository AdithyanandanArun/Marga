import { LineLayer, PolygonLayer, ScatterplotLayer, TextLayer } from '@deck.gl/layers';
import type { VehicleState, PedestrianState, DynamicActorObservation } from '../../types/canonical';
import { dimensions } from '../../simulation/vehicleBody';

const ACTOR_COLORS: Record<string, [number, number, number]> = {
  CAR: [34, 197, 94],
  BIKE: [251, 146, 60],
  AUTO: [234, 179, 8],
  BUS: [251, 146, 60],
  TRUCK: [251, 146, 60],
  AMBULANCE: [239, 68, 68],
  OTHER: [34, 197, 94],
};

const PEDESTRIAN_COLOR: [number, number, number] = [52, 211, 153];
const ANIMAL_COLOR: [number, number, number] = [251, 191, 36];
const SELECTED_COLOR: [number, number, number] = [255, 255, 255];

type VehicleBody = { vehicle: VehicleState; polygon: [number, number][]; nose: [[number, number], [number, number]] };
const METERS_PER_LAT = 111_320;
function vehicleBody(vehicle: VehicleState): VehicleBody {
  const heading = vehicle.heading_deg * Math.PI / 180;
  const { length, width } = dimensions(vehicle.actor_type);
  const north = Math.cos(heading);
  const east = Math.sin(heading);
  const sideNorth = -east;
  const sideEast = north;
  const lonScale = METERS_PER_LAT * Math.cos(vehicle.position.lat * Math.PI / 180);
  const at = (forward: number, side: number): [number, number] => [
    vehicle.position.lon + (east * forward + sideEast * side) / lonScale,
    vehicle.position.lat + (north * forward + sideNorth * side) / METERS_PER_LAT,
  ];
  return { vehicle, polygon: [at(length / 2, width / 2), at(length / 2, -width / 2), at(-length / 2, -width / 2), at(-length / 2, width / 2)], nose: [at(length / 2, 0), at(length / 2 + 1.2, 0)] };
}

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
        getFillColor: [234, 179, 8, 30],
        getLineColor: [234, 179, 8, 60],
        lineWidthMinPixels: 1,
        stroked: true,
        filled: true,
        pickable: false,
        radiusUnits: 'meters',
      }),
    );
  }

  const vehicleRadius = zoom > 16 ? 6 : zoom > 14 ? 5 : zoom > 12 ? 4 : 3;
  const bodies = vehicles.map(vehicleBody);

  layers.push(
    new PolygonLayer({
      id: 'vehicle-bodies', data: bodies,

      getPolygon: (d: VehicleBody) => d.polygon,
      getFillColor: (d: VehicleBody) => d.vehicle.actor_id === selectedId ? SELECTED_COLOR : (ACTOR_COLORS[d.vehicle.actor_type] ?? ACTOR_COLORS.OTHER),
      getLineColor: [15, 17, 23, 255],
      getLineWidth: 1.5,
      lineWidthUnits: 'pixels',
      stroked: true,
      filled: true,
      pickable: true,
      updateTriggers: {
        getFillColor: selectedId,
      },
    }),
  );
  layers.push(new LineLayer({ id: 'vehicle-direction', data: bodies, getSourcePosition: (d: VehicleBody) => d.nose[0], getTargetPosition: (d: VehicleBody) => d.nose[1], getColor: [255, 255, 255, 235], getWidth: 1.5, widthUnits: 'pixels', pickable: false }));
  const labelled = bodies.filter((d) => d.vehicle.actor_id === selectedId || d.vehicle.actor_id === 'ego_auto' || d.vehicle.actor_id === 'conflict_bus');
  layers.push(new TextLayer({ id: 'vehicle-labels', data: labelled, getPosition: (d: VehicleBody) => [d.vehicle.position.lon, d.vehicle.position.lat], getText: (d: VehicleBody) => d.vehicle.actor_type, getColor: [255, 255, 255, 245], getSize: 10, sizeUnits: 'pixels', getPixelOffset: [0, -14], getTextAnchor: 'middle', getAlignmentBaseline: 'bottom', billboard: true, pickable: false }));

  if (zoom > 13) {
    layers.push(
      new ScatterplotLayer({
        id: 'pedestrians',
        data: pedestrians,

        getPosition: (d: PedestrianState) => [d.position.lon, d.position.lat],
        getRadius: zoom > 15 ? 7 : 5,
        getFillColor: [...PEDESTRIAN_COLOR, 245] as [number, number, number, number],
        getLineColor: [220, 255, 235, 255],
        lineWidthMinPixels: 2,
        stroked: true,
        pickable: true,
        radiusUnits: 'pixels',
      }),
    );
    layers.push(new TextLayer({
      id: 'pedestrian-labels', data: pedestrians,
      getPosition: (d: PedestrianState) => [d.position.lon, d.position.lat],
      getText: () => 'P', getColor: [255, 255, 255, 255], getSize: 10,
      sizeUnits: 'pixels', getPixelOffset: [0, -9], getTextAnchor: 'middle',
      getAlignmentBaseline: 'bottom', billboard: true, pickable: false,
    }));
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
