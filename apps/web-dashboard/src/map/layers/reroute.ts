import { PathLayer } from '@deck.gl/layers';
import type { RouteChange } from '../../types/routes';

// Ali/visualization-owned. "A rerouted vehicle shows old/new routes" per
// final_imp.md — draws exactly the geometry Adithyan2's routing service
// published for this vehicle, nothing inferred or fabricated.

const OLD_ROUTE_COLOR: [number, number, number, number] = [155, 161, 176, 160];
const NEW_ROUTE_COLOR: [number, number, number, number] = [99, 102, 241, 230];

export function createRerouteLayer(change: RouteChange | undefined) {
  if (!change) return [];
  const toPath = (points: RouteChange['old_route']) => points.map((p) => [p.lon, p.lat] as [number, number]);

  return [
    new PathLayer({
      id: 'reroute-old',
      data: [{ path: toPath(change.old_route) }],
      getPath: (d: { path: [number, number][] }) => d.path,
      getColor: OLD_ROUTE_COLOR,
      getWidth: 2.5,
      widthUnits: 'pixels',
      pickable: false,
    }),
    new PathLayer({
      id: 'reroute-new',
      data: [{ path: toPath(change.new_route) }],
      getPath: (d: { path: [number, number][] }) => d.path,
      getColor: NEW_ROUTE_COLOR,
      getWidth: 4,
      widthUnits: 'pixels',
      capRounded: true,
      jointRounded: true,
      pickable: false,
    }),
  ];
}
