import { PathLayer } from '@deck.gl/layers';
import type { VehicleState } from '../../types/canonical';

const metersPerDegree = 111_320;
export function createTrajectoriesLayer(vehicles: VehicleState[]) {
  const paths = vehicles.map((vehicle) => {
    const heading = vehicle.heading_deg * Math.PI / 180;
    const points = [0, 1, 2, 3].map((seconds) => {
      const distance = vehicle.speed_mps * seconds;
      return [vehicle.position.lon + distance * Math.sin(heading) / (metersPerDegree * Math.cos(vehicle.position.lat * Math.PI / 180)), vehicle.position.lat + distance * Math.cos(heading) / metersPerDegree];
    });
    return { vehicle, points: points as [number, number][] };
  });
  return new PathLayer<{ vehicle: VehicleState; points: [number, number][] }>({ id: 'trajectories', data: paths, getPath: (d) => d.points,
    getColor: [74, 125, 255, 150], getWidth: 2, widthUnits: 'pixels', pickable: false });
}
