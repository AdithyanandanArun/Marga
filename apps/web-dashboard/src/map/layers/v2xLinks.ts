import { LineLayer } from '@deck.gl/layers';
import type { RSUState, VehicleState } from '../../types/canonical';

const earthRadiusM = 6_371_000;
function distanceM(a: VehicleState, b: RSUState): number {
  const dLat = (b.position.lat - a.position.lat) * Math.PI / 180;
  const dLon = (b.position.lon - a.position.lon) * Math.PI / 180;
  const lat1 = a.position.lat * Math.PI / 180;
  const lat2 = b.position.lat * Math.PI / 180;
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(lat1) * Math.cos(lat2) * Math.sin(dLon / 2) ** 2;
  return 2 * earthRadiusM * Math.asin(Math.sqrt(h));
}

export function createV2XLinksLayer(vehicles: VehicleState[], rsus: RSUState[]) {
  const links = vehicles.flatMap((vehicle) => rsus.filter((rsu) => distanceM(vehicle, rsu) <= rsu.coverage_m)
    .map((rsu) => ({ vehicle, rsu })));
  return new LineLayer({
    id: 'v2x-links', data: links,
    getSourcePosition: (d: { vehicle: VehicleState }) => [d.vehicle.position.lon, d.vehicle.position.lat],
    getTargetPosition: (d: { rsu: RSUState }) => [d.rsu.position.lon, d.rsu.position.lat],
    getColor: [167, 139, 250, 110], getWidth: 1, widthUnits: 'pixels', pickable: false,
  });
}
