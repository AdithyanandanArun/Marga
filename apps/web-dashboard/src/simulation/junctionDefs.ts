import { bezierArc, circle, fractionAtIndex, localPoint, projectPoint, type Point } from './geometry';

export type { Point };

export type JunctionType = 'CROSS' | 'T_JUNCTION' | 'ROUNDABOUT' | 'RAILWAY_CROSSING';

export type RouteControl = 'SIGNAL_GROUP_A' | 'SIGNAL_GROUP_B' | 'GATE' | 'NONE';

export interface RouteDef {
  id: string;
  path: Point[];
  /** Fraction along the path where a vehicle must hold if its control says stop. Null = never stops. */
  stopLineFraction: number | null;
  control: RouteControl;
}

export interface JunctionDefinition {
  type: JunctionType;
  label: string;
  description: string;
  center: Point;
  roads: Point[][];
  laneMarkings: Point[][];
  areaPolygons: { polygon: Point[]; fill: [number, number, number, number]; line: [number, number, number, number] }[];
  rails?: Point[][];
  sleepers?: Point[][];
  routes: RouteDef[];
  signal?: { groupA: Point; groupB: Point; greenMs: number; allRedMs: number };
  gate?: { position: Point; openMs: number; closedMs: number };
}

const ARM_LEN = 220;
const JUNCTION_R = 16;
const LANE_OFFSET = 3.2;
const RING_R = 22;
const ISLAND_R = 9;
const RAIL_ZONE = 6;

const BED = [20, 24, 32, 255] as [number, number, number, number];
const SURFACE = [54, 61, 72, 255] as [number, number, number, number];
const LANE_LINE = [225, 231, 239, 160] as [number, number, number, number];
const JUNCTION_FILL = [68, 76, 88, 230] as [number, number, number, number];
const JUNCTION_LINE = [225, 231, 239, 130] as [number, number, number, number];
const ISLAND_FILL = [40, 74, 48, 235] as [number, number, number, number];
const ISLAND_LINE = [120, 176, 128, 160] as [number, number, number, number];
const HAZARD_FILL = [120, 96, 12, 200] as [number, number, number, number];
const HAZARD_LINE = [234, 179, 8, 220] as [number, number, number, number];
const RAIL_COLOR = [148, 163, 184, 255] as [number, number, number, number];
const SLEEPER_COLOR = [90, 74, 58, 230] as [number, number, number, number];

interface ArmGeom {
  id: string;
  angle: number;
  far: Point;
  near: Point;
  inboundFar: Point;
  inboundNear: Point;
  outboundNear: Point;
  outboundFar: Point;
}

function buildArm(lat: number, lon: number, id: string, angle: number, armLen: number, radius: number, laneOffset: number): ArmGeom {
  return {
    id,
    angle,
    far: projectPoint(lat, lon, angle, armLen),
    near: projectPoint(lat, lon, angle, radius),
    inboundFar: projectPoint(lat, lon, angle, armLen, laneOffset),
    inboundNear: projectPoint(lat, lon, angle, radius, laneOffset),
    outboundNear: projectPoint(lat, lon, angle, radius, -laneOffset),
    outboundFar: projectPoint(lat, lon, angle, armLen, -laneOffset),
  };
}

function throughRoute(a: ArmGeom, b: ArmGeom, control: RouteControl, refLat: number): RouteDef {
  const path = [a.inboundFar, a.inboundNear, b.outboundNear, b.outboundFar];
  return {
    id: `${a.id}-${b.id}-through`,
    path,
    control,
    stopLineFraction: control === 'NONE' ? null : fractionAtIndex(path, 1, refLat),
  };
}

function turnRoute(a: ArmGeom, b: ArmGeom, center: Point, control: RouteControl, refLat: number): RouteDef {
  const arc = bezierArc(a.inboundNear, center, b.outboundNear, 8);
  const path = [a.inboundFar, ...arc, b.outboundFar];
  return {
    id: `${a.id}-${b.id}-turn`,
    path,
    control,
    stopLineFraction: control === 'NONE' ? null : fractionAtIndex(path, 1, refLat),
  };
}

function bypassRoute(a: ArmGeom, b: ArmGeom, via: Point[], refLat: number): RouteDef {
  const path = [a.inboundFar, ...via, b.outboundFar];
  return {
    id: `${a.id}-${b.id}-service-bypass`,
    path,
    control: 'NONE',
    stopLineFraction: null,
  };
}

function laneMarkingsFor(arms: ArmGeom[]): Point[][] {
  return arms.map((arm) => [arm.far, arm.near]);
}

/** Cross junction: four arms, full through + turn movements, two-phase signal. */
function buildCross(lat: number, lon: number): JunctionDefinition {
  const center: Point = localPoint(lat, lon, 0, 0);
  const N = buildArm(lat, lon, 'N', 0, ARM_LEN, JUNCTION_R, LANE_OFFSET);
  const E = buildArm(lat, lon, 'E', 90, ARM_LEN, JUNCTION_R, LANE_OFFSET);
  const S = buildArm(lat, lon, 'S', 180, ARM_LEN, JUNCTION_R, LANE_OFFSET);
  const W = buildArm(lat, lon, 'W', 270, ARM_LEN, JUNCTION_R, LANE_OFFSET);
  const groupOf = (armId: string): RouteControl => (armId === 'N' || armId === 'S' ? 'SIGNAL_GROUP_A' : 'SIGNAL_GROUP_B');

  const routes: RouteDef[] = [];
  const arms = [N, E, S, W];
  const oppositeOf: Record<string, ArmGeom> = { N: S, S: N, E: W, W: E };
  for (const a of arms) {
    routes.push(throughRoute(a, oppositeOf[a.id], groupOf(a.id), lat));
    for (const b of arms) {
      if (b.id === a.id || b.id === oppositeOf[a.id].id) continue;
      routes.push(turnRoute(a, b, center, groupOf(a.id), lat));
    }
  }

  const box = JUNCTION_R;
  return {
    type: 'CROSS',
    label: 'Four-way cross',
    description: 'Two-phase signal. NS and EW alternate; through and turning traffic share each phase.',
    center,
    roads: [
      [W.far, E.far],
      [N.far, S.far],
    ],
    laneMarkings: laneMarkingsFor(arms),
    areaPolygons: [{
      polygon: [
        localPoint(lat, lon, -box, -box), localPoint(lat, lon, -box, box),
        localPoint(lat, lon, box, box), localPoint(lat, lon, box, -box),
      ],
      fill: JUNCTION_FILL, line: JUNCTION_LINE,
    }],
    routes,
    signal: {
      groupA: N.near,
      groupB: E.near,
      greenMs: 7000,
      allRedMs: 1200,
    },
  };
}

/** T-junction: through road (E-W) plus a stub (N) that must give way via a short-green minor phase. */
function buildT(lat: number, lon: number): JunctionDefinition {
  const center: Point = localPoint(lat, lon, 0, 0);
  const N = buildArm(lat, lon, 'N', 0, ARM_LEN, JUNCTION_R, LANE_OFFSET);
  const E = buildArm(lat, lon, 'E', 90, ARM_LEN, JUNCTION_R, LANE_OFFSET);
  const W = buildArm(lat, lon, 'W', 270, ARM_LEN, JUNCTION_R, LANE_OFFSET);
  const arms = [N, E, W];

  const routes: RouteDef[] = [
    throughRoute(E, W, 'SIGNAL_GROUP_A', lat),
    throughRoute(W, E, 'SIGNAL_GROUP_A', lat),
    turnRoute(N, E, center, 'SIGNAL_GROUP_B', lat),
    turnRoute(N, W, center, 'SIGNAL_GROUP_B', lat),
    turnRoute(E, N, center, 'SIGNAL_GROUP_A', lat),
    turnRoute(W, N, center, 'SIGNAL_GROUP_A', lat),
  ];
  // An asymmetric residential/service road sits beside the peripheral
  // T-junction. It provides a genuine alternate E↔W movement when the main
  // approach is queued, without turning the central junction into a ring.
  const serviceSouthWest = localPoint(lat, lon, -52, -72);
  const serviceSouthEast = localPoint(lat, lon, -52, 72);
  routes.push(
    bypassRoute(E, W, [serviceSouthEast, serviceSouthWest], lat),
    bypassRoute(W, E, [serviceSouthWest, serviceSouthEast], lat),
  );

  const box = JUNCTION_R;
  return {
    type: 'T_JUNCTION',
    label: 'T-junction',
    description: 'Main E-W road runs long green; the stub road gets a short window and queues often.',
    center,
    roads: [
      [W.far, E.far],
      [N.far, center],
      [W.far, serviceSouthWest, serviceSouthEast, E.far],
    ],
    laneMarkings: laneMarkingsFor(arms),
    areaPolygons: [{
      polygon: [
        localPoint(lat, lon, -box, -box), localPoint(lat, lon, -box, box),
        localPoint(lat, lon, box, box), localPoint(lat, lon, box, -box),
      ],
      fill: JUNCTION_FILL, line: JUNCTION_LINE,
    }],
    routes,
    signal: {
      groupA: E.near,
      groupB: N.near,
      greenMs: 8500,
      allRedMs: 1000,
    },
  };
}

/** Roundabout: four arms feed a clockwise ring (left-hand traffic); free-flowing, no stop line. */
function buildRoundabout(lat: number, lon: number): JunctionDefinition {
  const center: Point = localPoint(lat, lon, 0, 0);
  const angles = [0, 90, 180, 270];
  const armIds = ['N', 'E', 'S', 'W'];
  const arms = angles.map((angle, index) => buildArm(lat, lon, armIds[index], angle, ARM_LEN, RING_R, LANE_OFFSET));
  const ringPoint = (angle: number) => projectPoint(lat, lon, angle, RING_R);

  const routes: RouteDef[] = [];
  for (let i = 0; i < angles.length; i++) {
    for (let j = 0; j < angles.length; j++) {
      if (i === j) continue;
      const entryAngle = angles[i];
      const exitAngle = angles[j];
      const sweep = ((exitAngle - entryAngle + 360) % 360) || 360;
      const steps = Math.max(4, Math.round(sweep / 15));
      const arc: Point[] = [];
      for (let s = 0; s <= steps; s++) {
        arc.push(ringPoint(entryAngle + (sweep * s) / steps));
      }
      // The outer endpoints deliberately use the same lane offsets as every
      // connected road. A vehicle leaving the cross junction therefore enters
      // the roundabout on the exact same lane rather than teleporting to its
      // centreline.
      const path = [arms[i].inboundFar, ringPoint(entryAngle), ...arc.slice(1), arms[j].outboundFar];
      routes.push({ id: `${armIds[i]}-${armIds[j]}-ring`, path, control: 'NONE', stopLineFraction: null });
    }
  }

  return {
    type: 'ROUNDABOUT',
    label: 'Roundabout',
    description: 'Free-flowing circulation, clockwise (left-hand traffic). No signal — speed is the only variable.',
    center,
    roads: arms.map((arm) => [arm.far, ringPoint(arm.angle)]),
    laneMarkings: arms.map((arm) => [arm.far, ringPoint(arm.angle)]),
    areaPolygons: [
      { polygon: circle(lat, lon, RING_R + 5.5, 40), fill: SURFACE, line: JUNCTION_LINE },
      { polygon: circle(lat, lon, ISLAND_R, 28), fill: ISLAND_FILL, line: ISLAND_LINE },
    ],
    routes,
  };
}

/** Level (railway) crossing: one road crossing a single track at grade, with a timed gate. */
function buildRailwayCrossing(lat: number, lon: number): JunctionDefinition {
  const center: Point = localPoint(lat, lon, 0, 0);
  const E = buildArm(lat, lon, 'E', 90, ARM_LEN, RAIL_ZONE, LANE_OFFSET);
  const W = buildArm(lat, lon, 'W', 270, ARM_LEN, RAIL_ZONE, LANE_OFFSET);

  const routes: RouteDef[] = [
    throughRoute(E, W, 'GATE', lat),
    throughRoute(W, E, 'GATE', lat),
  ];

  const railHalfLen = 90;
  const rails: Point[][] = [
    [projectPoint(lat, lon, 0, railHalfLen, 0.75), projectPoint(lat, lon, 180, railHalfLen, -0.75)],
    [projectPoint(lat, lon, 0, railHalfLen, -0.75), projectPoint(lat, lon, 180, railHalfLen, 0.75)],
  ];
  const sleepers: Point[][] = [];
  for (let d = -railHalfLen; d <= railHalfLen; d += 3) {
    sleepers.push([
      projectPoint(lat, lon, 0, d, 1.6),
      projectPoint(lat, lon, 0, d, -1.6),
    ]);
  }

  const boxHalfLen = RAIL_ZONE + 1.5;
  const boxHalfWidth = 5.5;
  return {
    type: 'RAILWAY_CROSSING',
    label: 'Railway level crossing',
    description: 'Gate cycles open/closed on a timer. Traffic must fully stop while the crossing is closed.',
    center,
    roads: [[W.far, E.far]],
    laneMarkings: [[W.far, W.near], [E.far, E.near]],
    areaPolygons: [{
      polygon: [
        localPoint(lat, lon, -boxHalfWidth, -boxHalfLen), localPoint(lat, lon, -boxHalfWidth, boxHalfLen),
        localPoint(lat, lon, boxHalfWidth, boxHalfLen), localPoint(lat, lon, boxHalfWidth, -boxHalfLen),
      ],
      fill: HAZARD_FILL, line: HAZARD_LINE,
    }],
    rails,
    sleepers,
    routes,
    gate: {
      position: localPoint(lat, lon, 8, 0),
      openMs: 35000,
      closedMs: 30000,
    },
  };
}

export function buildJunction(type: JunctionType, lat: number, lon: number): JunctionDefinition {
  switch (type) {
    case 'CROSS': return buildCross(lat, lon);
    case 'T_JUNCTION': return buildT(lat, lon);
    case 'ROUNDABOUT': return buildRoundabout(lat, lon);
    case 'RAILWAY_CROSSING': return buildRailwayCrossing(lat, lon);
  }
}

export const JUNCTION_TYPES: JunctionType[] = ['CROSS', 'T_JUNCTION', 'ROUNDABOUT', 'RAILWAY_CROSSING'];

export const BED_COLOR = BED;
export const SURFACE_COLOR = SURFACE;
export const LANE_LINE_COLOR = LANE_LINE;
export const RAIL_TRACK_COLOR = RAIL_COLOR;
export const SLEEPER_TIE_COLOR = SLEEPER_COLOR;
