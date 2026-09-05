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
  /** Zebra stripes rendered across each pedestrian crossing approach. */
  crosswalks: Point[][];
  areaPolygons: { polygon: Point[]; fill: [number, number, number, number]; line: [number, number, number, number] }[];
  rails?: Point[][];
  sleepers?: Point[][];
  routes: RouteDef[];
  signal?: { groupA: Point; groupB: Point; greenMs: number; allRedMs: number };
  gate?: {
    position: Point;
    openMs: number;
    closedMs: number;
    /** Warning period before the gate drops, equivalent to the bell and amber
     * light at a real level crossing. Traffic that has not yet crossed the stop
     * line must hold, so nothing new commits to the track late. */
    warningMs: number;
    /** Distance from the junction centre that counts as being on the track. A
     * vehicle inside this radius when the gate drops must clear it, never stop
     * on it. */
    clearanceRadiusM: number;
  };
}

const ARM_LEN = 220;
const JUNCTION_R = 16;
const LANE_OFFSET = 3.2;
// Two concentric circulating lanes. Both radii stay inside the engine's 26 m
// junction conflict zone so that circulating traffic always counts as "inside
// the junction" and entering traffic (still out on a 220 m arm) counts as
// approaching. That is what makes the give-way rule — circulating before
// entering — actually discriminate here; on the old 22 m single ring the zone
// was larger than the ring, so every vehicle read as inside and the rule was
// inert.
//
// The 8 m radial gap exceeds the 6.5 m conflict distance, so an inner and an
// outer vehicle running side by side are not read as a conflict.
const OUTER_RING_R = 23;
const INNER_RING_R = 15;
const ISLAND_R = 10;
/** Angular offset of an arm's entry and exit either side of its centreline.
 * Wide enough to seat a splitter island between the two lanes. */
const ARM_SPLIT_DEG = 12;
const RAIL_ZONE = 6;
/** Where traffic holds for a closed gate. This must sit clear of the crossing
 * box (half-length RAIL_ZONE + 1.5 = 7.5 m), otherwise a vehicle that has
 * correctly stopped for a red gate is still standing on the rails. */
const RAIL_STOP_R = 15;

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

function laneMarkingsFor(arms: ArmGeom[]): Point[][] {
  return arms.map((arm) => [arm.far, arm.near]);
}

/** Build geometry from the road centreline, not fixed map coordinates. This
 * keeps the feature valid when the network origin is moved or another region
 * is imported. */
function crosswalksForArms(lat: number, lon: number, angles: number[], radius: number): Point[][] {
  const stripes: Point[][] = [];
  for (const angle of angles) {
    for (let offset = -4; offset <= 4; offset += 2) {
      stripes.push([
        projectPoint(lat, lon, angle, radius + offset, -7),
        projectPoint(lat, lon, angle, radius + offset, 7),
      ]);
    }
  }
  return stripes;
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
    crosswalks: crosswalksForArms(lat, lon, arms.map((arm) => arm.angle), JUNCTION_R + 8),
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
  const box = JUNCTION_R;
  return {
    type: 'T_JUNCTION',
    label: 'T-junction',
    description: 'Main E-W road runs long green; the stub road gets a short window and queues often.',
    center,
    roads: [
      [W.far, E.far],
      [N.far, center],
    ],
    laneMarkings: laneMarkingsFor(arms),
    crosswalks: crosswalksForArms(lat, lon, arms.map((arm) => arm.angle), JUNCTION_R + 8),
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

/**
 * Roundabout: four arms feed a two-lane clockwise ring (left-hand traffic).
 *
 * The lane is decided at the entry from the chosen exit, which is the property
 * that makes a turbo roundabout deadlock-resistant: no vehicle ever has to
 * weave across another to reach its exit. The physical dividers of a true
 * turbo are deliberately left out — the lane line is painted and crossable, so
 * two-wheelers and autos can still filter between lanes, which is how an
 * Indian city circle actually behaves. Route geometry never *requires* a
 * crossing; driver behaviour may still produce one.
 *
 * The invariant that makes this work is that the outer lane always leaves at
 * the next arm. Traffic staying on the ring is therefore in the inner lane and
 * never meets entering or exiting traffic. A vehicle bound for a later arm
 * changes lane exactly twice — inward before the next exit, outward after the
 * last entry — and on that final stretch every other outer-lane vehicle shares
 * its exit, so it is a merge rather than a crossing.
 *
 * Entry and exit are distinct points either side of each arm's centreline,
 * with a splitter island between them. On the previous design both were the
 * same coordinate, so entering and exiting traffic met head-on at every arm.
 */
function buildRoundabout(lat: number, lon: number): JunctionDefinition {
  const center: Point = localPoint(lat, lon, 0, 0);
  const angles = [0, 90, 180, 270];
  const armIds = ['N', 'E', 'S', 'W'];
  const arms = angles.map((angle, index) => buildArm(lat, lon, armIds[index], angle, ARM_LEN, OUTER_RING_R, LANE_OFFSET));
  const ringPoint = (angle: number, radius: number) => projectPoint(lat, lon, angle, radius);
  // Circulation is clockwise, so bearing increases along the direction of
  // travel. An arm's exit is therefore reached before its entry, which is why
  // the exit sits at the lower bearing.
  const entryAngle = (index: number) => angles[index] + ARM_SPLIT_DEG;
  const exitAngle = (index: number) => angles[index] - ARM_SPLIT_DEG;

  /** Arc from one bearing to the next going clockwise, interpolating the
   * radius so a lane change is a smooth spiral rather than a step sideways. */
  const spiral = (fromDeg: number, toDeg: number, fromR: number, toR: number): Point[] => {
    const sweep = (((toDeg - fromDeg) % 360) + 360) % 360;
    const steps = Math.max(3, Math.round(sweep / 8));
    const points: Point[] = [];
    for (let s = 0; s <= steps; s++) {
      const t = s / steps;
      points.push(ringPoint(fromDeg + sweep * t, fromR + (toR - fromR) * t));
    }
    return points;
  };

  const routes: RouteDef[] = [];
  for (let i = 0; i < angles.length; i++) {
    for (let j = 0; j < angles.length; j++) {
      if (i === j) continue;
      const armsPassed = (j - i + angles.length) % angles.length;
      const start = entryAngle(i);
      const end = exitAngle(j);
      let ring: Point[];
      if (armsPassed === 1) {
        // Leaving at the very next arm: stay in the outer lane throughout.
        // This is the only traffic the outer lane ever carries.
        ring = spiral(start, end, OUTER_RING_R, OUTER_RING_R);
      } else {
        // Dive inside before the next arm's exit, so this vehicle is already
        // out of the outer lane by the time it passes traffic leaving there.
        const dive = exitAngle((i + 1) % angles.length);
        // Come back out only after the last arm's entry. Everything in the
        // outer lane from here on is leaving at the same exit.
        const merge = entryAngle((j + angles.length - 1) % angles.length);
        ring = [
          ...spiral(start, dive, OUTER_RING_R, INNER_RING_R),
          ...spiral(dive, merge, INNER_RING_R, INNER_RING_R).slice(1),
          ...spiral(merge, end, INNER_RING_R, OUTER_RING_R).slice(1),
        ];
      }
      // The outer endpoints deliberately use the same lane offsets as every
      // connected road. A vehicle leaving the cross junction therefore enters
      // the roundabout on the exact same lane rather than teleporting to its
      // centreline.
      const path = [
        arms[i].inboundFar, arms[i].inboundNear,
        ...ring,
        arms[j].outboundNear, arms[j].outboundFar,
      ];
      routes.push({ id: `${armIds[i]}-${armIds[j]}-ring`, path, control: 'NONE', stopLineFraction: null });
    }
  }

  // A splitter island per arm, seated between the entry and exit lanes. It is
  // what physically separates the two on a real rotary, and it is why the
  // entry and exit can no longer occupy the same point.
  const splitters = angles.map((angle) => ({
    polygon: [
      projectPoint(lat, lon, angle, OUTER_RING_R + 2.0, 0),
      projectPoint(lat, lon, angle, OUTER_RING_R + 7.0, 1.4),
      projectPoint(lat, lon, angle, OUTER_RING_R + 30.0, 1.4),
      projectPoint(lat, lon, angle, OUTER_RING_R + 30.0, -1.4),
      projectPoint(lat, lon, angle, OUTER_RING_R + 7.0, -1.4),
    ],
    fill: ISLAND_FILL,
    line: ISLAND_LINE,
  }));

  return {
    type: 'ROUNDABOUT',
    label: 'Roundabout',
    description: 'Two-lane clockwise circulation (left-hand traffic). Lane is set at entry by the chosen exit, so through traffic never weaves across exiting traffic. The lane line is painted, not kerbed — bikes and autos can still filter.',
    center,
    roads: arms.map((arm) => [arm.far, arm.near]),
    laneMarkings: [
      ...arms.map((arm) => [arm.far, arm.near]),
      // Painted, crossable lane line between the two circulating lanes.
      circle(lat, lon, (INNER_RING_R + OUTER_RING_R) / 2, 48),
    ],
    crosswalks: crosswalksForArms(lat, lon, angles, OUTER_RING_R + 31),
    areaPolygons: [
      { polygon: circle(lat, lon, OUTER_RING_R + 5.5, 48), fill: SURFACE, line: JUNCTION_LINE },
      { polygon: circle(lat, lon, ISLAND_R, 32), fill: ISLAND_FILL, line: ISLAND_LINE },
      ...splitters,
    ],
    routes,
  };
}

/** Level (railway) crossing: one road crossing a single track at grade, with a timed gate. */
function buildRailwayCrossing(lat: number, lon: number): JunctionDefinition {
  const center: Point = localPoint(lat, lon, 0, 0);
  const E = buildArm(lat, lon, 'E', 90, ARM_LEN, RAIL_ZONE, LANE_OFFSET);
  const W = buildArm(lat, lon, 'W', 270, ARM_LEN, RAIL_ZONE, LANE_OFFSET);

  // The stop line is an explicit node well short of the rails, rather than the
  // generic throughRoute stop at the junction edge. That generic line sits
  // 6 m from the centre, inside the 7.5 m crossing box, so queued traffic used
  // to wait *on* the track.
  const railRoute = (a: ArmGeom, b: ArmGeom): RouteDef => {
    const hold = projectPoint(lat, lon, a.angle, RAIL_STOP_R, LANE_OFFSET);
    const path = [a.inboundFar, hold, a.inboundNear, b.outboundNear, b.outboundFar];
    return {
      id: `${a.id}-${b.id}-through`,
      path,
      control: 'GATE',
      stopLineFraction: fractionAtIndex(path, 1, lat),
    };
  };
  const routes: RouteDef[] = [railRoute(E, W), railRoute(W, E)];

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
    crosswalks: crosswalksForArms(lat, lon, [90, 270], RAIL_STOP_R + 3),
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
      // Long enough for the slowest vehicle (a truck at ~1.1 m/s^2) to cover
      // the 22.5 m from the stop line to the far edge of the box.
      warningMs: 4000,
      clearanceRadiusM: boxHalfLen + 2.5,
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
