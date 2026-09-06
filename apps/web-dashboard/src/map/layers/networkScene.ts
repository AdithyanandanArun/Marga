import { PathLayer, PolygonLayer } from '@deck.gl/layers';
import { buildJunctionNetwork } from '../../simulation/networkEngine';
import {
  BED_COLOR, LANE_LINE_COLOR, RAIL_TRACK_COLOR, SLEEPER_TIE_COLOR, SURFACE_COLOR, type Point,
} from '../../simulation/junctionDefs';

// This is the authoritative display geometry for both the live Control Center
// and the simulator. Telemetry from the network adapter is generated in this
// same coordinate frame, so actors cannot drift onto an unrelated basemap.
export const NETWORK_LAT = 12.9550;
export const NETWORK_LON = 77.6200;
export const NETWORK_CENTER: Point = [NETWORK_LON, NETWORK_LAT];

const network = buildJunctionNetwork(NETWORK_LAT, NETWORK_LON);
const roads = [...network.junctions.flatMap((junction) => junction.roads), ...network.bypassRoads];
const bypassRoads = network.bypassRoads;
const laneMarkings = [...network.junctions.flatMap((junction) => junction.laneMarkings), ...network.bypassLaneMarkings];
const stopLines = network.bypassStopLines;
const areas = network.junctions.flatMap((junction) => junction.areaPolygons);
const rails = network.junctions.flatMap((junction) => junction.rails ?? []);
const sleepers = network.junctions.flatMap((junction) => junction.sleepers ?? []);
const crosswalks = network.junctions.flatMap((junction) => junction.crosswalks);
const allPoints = [
  ...roads.flat(), ...laneMarkings.flat(), ...stopLines.flat(), ...areas.flatMap((area) => area.polygon), ...rails.flat(), ...sleepers.flat(), ...crosswalks.flat(),
];
export const NETWORK_BOUNDS: [[number, number], [number, number]] = [
  [Math.min(...allPoints.map((point) => point[0])), Math.min(...allPoints.map((point) => point[1]))],
  [Math.max(...allPoints.map((point) => point[0])), Math.max(...allPoints.map((point) => point[1]))],
];

let cachedRoadLayers: ReturnType<typeof buildNetworkRoadLayers> | null = null;

function buildNetworkRoadLayers() {
  return [
    new PathLayer({ id: 'network-road-bed', data: roads, getPath: (d: Point[]) => d, getColor: BED_COLOR, getWidth: 13, widthUnits: 'meters', capRounded: true, jointRounded: true, pickable: false }),
    new PathLayer({ id: 'network-road-surface', data: roads, getPath: (d: Point[]) => d, getColor: SURFACE_COLOR, getWidth: 10, widthUnits: 'meters', capRounded: true, jointRounded: true, pickable: false }),
    // Give the diversion corridor a clearly visible paved surface. The thin
    // green/yellow/red line remains a live load overlay, never the road body.
    new PathLayer({ id: 'network-bypass-bed', data: bypassRoads, getPath: (d: Point[]) => d, getColor: [32, 41, 55, 255], getWidth: 18, widthUnits: 'meters', capRounded: true, jointRounded: true, pickable: false }),
    new PathLayer({ id: 'network-bypass-surface', data: bypassRoads, getPath: (d: Point[]) => d, getColor: [100, 113, 132, 255], getWidth: 14, widthUnits: 'meters', capRounded: true, jointRounded: true, pickable: false }),
    new PathLayer({ id: 'network-bypass-centre-line', data: bypassRoads, getPath: (d: Point[]) => d, getColor: [248, 250, 252, 245], getWidth: 1.1, widthUnits: 'meters', capRounded: true, jointRounded: true, pickable: false }),
    new PathLayer({ id: 'network-lane-markings', data: laneMarkings, getPath: (d: Point[]) => d, getColor: LANE_LINE_COLOR, getWidth: 0.6, widthUnits: 'meters', capRounded: true, jointRounded: true, pickable: false }),
    new PathLayer({ id: 'network-bypass-stop-lines', data: stopLines, getPath: (d: Point[]) => d, getColor: [245, 245, 245, 235], getWidth: 0.8, widthUnits: 'meters', capRounded: true, pickable: false }),
    new PolygonLayer({ id: 'network-junction-areas', data: areas, getPolygon: (d: typeof areas[number]) => d.polygon, getFillColor: (d: typeof areas[number]) => d.fill, getLineColor: (d: typeof areas[number]) => d.line, getLineWidth: 1, lineWidthUnits: 'pixels', stroked: true, filled: true, pickable: false }),
    new PathLayer({ id: 'network-zebra-crossings', data: crosswalks, getPath: (d: Point[]) => d, getColor: [245, 245, 245, 220], getWidth: 1.2, widthUnits: 'meters', pickable: false }),
    ...(rails.length ? [new PathLayer({ id: 'network-rails', data: rails, getPath: (d: Point[]) => d, getColor: RAIL_TRACK_COLOR, getWidth: 0.18, widthUnits: 'meters', pickable: false })] : []),
    ...(sleepers.length ? [new PathLayer({ id: 'network-sleepers', data: sleepers, getPath: (d: Point[]) => d, getColor: SLEEPER_TIE_COLOR, getWidth: 0.35, widthUnits: 'meters', pickable: false })] : []),
  ];
}

/** The road district never changes during a live frame. Reusing these Deck
 * layers avoids re-tessellating every road, crossing and rail sleeper at the
 * display refresh rate. Dynamic actors and signals remain separate layers. */
export function createNetworkRoadLayers() {
  cachedRoadLayers ??= buildNetworkRoadLayers();
  return cachedRoadLayers;
}
