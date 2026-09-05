import { PathLayer, PolygonLayer } from '@deck.gl/layers';

const LAT = 12.9822;
const LON = 77.5935;
const METERS_PER_LAT = 111_320;
const METERS_PER_LON = 108_500;
type Point = [number, number];

const point = (northM: number, eastM: number): Point => [LON + eastM / METERS_PER_LON, LAT + northM / METERS_PER_LAT];
const horizontal = (northM: number): Point[] => [point(northM, -300), point(northM, -65), point(0, 0), point(-northM, 65), point(-northM, 300)];
const vertical = (eastM: number): Point[] => [point(-300, eastM), point(-65, eastM), point(0, 0), point(65, -eastM), point(300, -eastM)];

const roads = [
  { path: [point(0, -310), point(0, 310)] },
  { path: [point(-310, 0), point(310, 0)] },
];
const lanes = [horizontal(6), horizontal(10), horizontal(-6), horizontal(-10), vertical(-6), vertical(6), vertical(-10), vertical(10)];

/** The explicit, canonical geometry for the Bangalore junction demo scene.
 * It is intentionally independent of a third-party basemap: demo actors and
 * lane markings share this local coordinate frame, so an actor cannot appear
 * to drive through a building because external map geometry disagrees. */
export function createJunctionRoadLayers() {
  return [
    new PathLayer({ id: 'junction-road-bed', data: roads, getPath: (d: { path: Point[] }) => d.path, getColor: [20, 24, 32, 255], getWidth: 50, widthUnits: 'meters', capRounded: true, jointRounded: true, pickable: false }),
    new PathLayer({ id: 'junction-road-surface', data: roads, getPath: (d: { path: Point[] }) => d.path, getColor: [54, 61, 72, 255], getWidth: 43, widthUnits: 'meters', capRounded: true, jointRounded: true, pickable: false }),
    new PathLayer({ id: 'junction-lanes', data: lanes, getPath: (d: Point[]) => d, getColor: [225, 231, 239, 200], getWidth: 0.7, widthUnits: 'meters', capRounded: true, jointRounded: true, pickable: false }),
    new PolygonLayer({ id: 'junction-box', data: [{ polygon: [point(-15, -15), point(-15, 15), point(15, 15), point(15, -15)] }], getPolygon: (d: { polygon: Point[] }) => d.polygon, getFillColor: [68, 76, 88, 230], getLineColor: [225, 231, 239, 120], getLineWidth: 1, lineWidthUnits: 'pixels', stroked: true, pickable: false }),
  ];
}
