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
  // A short approach can be operationally congested long before it reaches
  // 100% physical capacity. Keep green only for genuinely free flow, make a
  // visible yellow warning at 15%, and reserve red for a sustained 45%+ load.
  const [from, to, t] = clamped < 0.15
    ? [FREE_FLOW, FREE_FLOW, 0]
    : clamped < 0.45
      ? [FREE_FLOW, MODERATE, (clamped - 0.15) / 0.3]
      : [MODERATE, CONGESTED, (clamped - 0.45) / 0.55];
  return [
    Math.round(lerp(from[0], to[0], t)),
    Math.round(lerp(from[1], to[1], t)),
    Math.round(lerp(from[2], to[2], t)),
    210,
  ];
}

function bounded(value: number): number {
  return Math.max(0, Math.min(1, value));
}

/**
 * Convert measured edge conditions into a visually useful congestion score.
 * Capacity alone is deliberately not enough: stationary queues and low speed
 * are earlier, real signs of congestion on Indian junction approaches.
 */
function graphCongestionScore(edge: GraphEdgeMetrics): number {
  if (edge.vehicle_count === 0) return 0;
  const capacityPressure = bounded(edge.capacity_ratio / 0.7);
  const queuePressure = bounded(edge.queue_length / Math.max(2, edge.capacity_vehicles * 0.22));
  const speedPressure = bounded((6 - edge.avg_speed_mps) / 6);
  const downstreamPressure = bounded(edge.downstream_congestion);
  const hazardPressure = bounded(edge.hazard_penalty);
  // The maximum prevents a stopped queue or blocked downstream segment from
  // being hidden by an otherwise low edge-wide average. The weighted term
  // avoids a single noisy value producing a full-red road by itself.
  const combined = (
    capacityPressure * 0.34
    + queuePressure * 0.30
    + speedPressure * 0.20
    + downstreamPressure * 0.11
    + hazardPressure * 0.05
  );
  return bounded(Math.max(combined, queuePressure * 0.85, downstreamPressure * 0.8));
}

/** Load in [0,1]: derive it from published graph evidence when available,
 * otherwise use the same observed count/speed/queue signals from canonical
 * vehicle telemetry. Returns null when there is genuinely nothing to show. */
function segmentLoad(segmentId: string, vehicles: VehicleState[], graphEdges: Map<string, GraphEdgeMetrics>): number | null {
  const edge = graphEdges.get(segmentId);
  if (edge) return graphCongestionScore(edge);
  const onSegment = vehicles.filter((v) => v.road_segment_id === segmentId);
  if (onSegment.length === 0) return null;
  const queued = onSegment.filter((vehicle) => vehicle.speed_mps <= 2).length;
  const avgSpeed = onSegment.reduce((total, vehicle) => total + vehicle.speed_mps, 0) / onSegment.length;
  const countPressure = bounded(onSegment.length / 4);
  const queuePressure = bounded(queued / 2);
  const speedPressure = bounded((6 - avgSpeed) / 6);
  return bounded(Math.max(
    countPressure * 0.5 + queuePressure * 0.3 + speedPressure * 0.2,
    queuePressure * 0.85,
  ));
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
