import { PathLayer } from '@deck.gl/layers';
import { buildJunctionNetwork } from '../../simulation/networkEngine';
import { NETWORK_LAYOUT } from '../../simulation/networkEngine';
import type { Point } from '../../simulation/junctionDefs';
import type { VehicleState } from '../../types/canonical';
import type { GraphEdgeMetrics } from '../../types/graph';
import { NETWORK_LAT, NETWORK_LON } from './networkScene';

// Ali/visualization-owned. "Roads use load colours" per final_imp.md — driven
// by Adithyan1's mobility graph the moment it publishes an edge for this
// segment id; until then, coloured from the same real vehicle telemetry the
// rest of the dashboard already renders (never a fabricated value). A segment
// with no data at all — no graph edge, no vehicles — gets no colour overlay.

const network = buildJunctionNetwork(NETWORK_LAT, NETWORK_LON);

interface RoadSegment {
  segmentId: string;
  path: Point[];
}

const SEGMENTS: RoadSegment[] = network.junctions.flatMap((junction, index) => {
  const junctionId = `network-${NETWORK_LAYOUT[index].id}`;
  return junction.routes.map((route) => ({ segmentId: `${junctionId}:${route.id}`, path: route.path }));
});

// Free-flow green -> congested red, matching the severity palette used
// elsewhere (var(--severity-*) in index.css) so load colour reads the same
// language as every other risk/severity cue in the app.
const FREE_FLOW: [number, number, number] = [34, 197, 94];
const MODERATE: [number, number, number] = [234, 179, 8];
const CONGESTED: [number, number, number] = [239, 68, 68];

function lerp(a: number, b: number, t: number): number {
  return a + (b - a) * t;
}

function loadColor(load: number): [number, number, number, number] {
  const clamped = Math.max(0, Math.min(1, load));
  const [from, to, t] = clamped < 0.5 ? [FREE_FLOW, MODERATE, clamped * 2] : [MODERATE, CONGESTED, (clamped - 0.5) * 2];
  return [
    Math.round(lerp(from[0], to[0], t)),
    Math.round(lerp(from[1], to[1], t)),
    Math.round(lerp(from[2], to[2], t)),
    210,
  ];
}

/** Load in [0,1]: prefer the canonical graph edge's capacity_ratio (a real,
 * published signal), otherwise derive a rough proxy from how many live
 * vehicles are actually on this segment right now. Returns null when there
 * is genuinely nothing to show — never a guessed default. */
function segmentLoad(segmentId: string, vehicles: VehicleState[], graphEdges: Map<string, GraphEdgeMetrics>): number | null {
  const edge = graphEdges.get(segmentId);
  if (edge) return Math.max(0, Math.min(1, edge.capacity_ratio));
  const onSegment = vehicles.filter((v) => v.road_segment_id === segmentId);
  if (onSegment.length === 0) return null;
  // A short junction approach carrying more than ~6 vehicles at once reads as
  // saturated in this demo's scale — a coarse stand-in for capacity_ratio.
  return Math.min(1, onSegment.length / 6);
}

export function createEdgeLoadLayer(vehicles: VehicleState[], graphEdges: Map<string, GraphEdgeMetrics>) {
  const colored = SEGMENTS
    .map((segment) => {
      const load = segmentLoad(segment.segmentId, vehicles, graphEdges);
      return load === null ? null : { edge_id: segment.segmentId, path: segment.path, color: loadColor(load) };
    })
    .filter((entry): entry is { edge_id: string; path: Point[]; color: [number, number, number, number] } => entry !== null);

  if (colored.length === 0) return [];

  return [
    new PathLayer({
      id: 'edge-load',
      data: colored,
      getPath: (d: { path: Point[] }) => d.path,
      getColor: (d: { color: [number, number, number, number] }) => d.color,
      getWidth: 2.4,
      widthUnits: 'meters',
      capRounded: true,
      jointRounded: true,
      pickable: true,
    }),
  ];
}
