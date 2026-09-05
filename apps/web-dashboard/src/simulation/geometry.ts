// Pure geometry helpers for the isolated junction simulator. No store, no
// network — everything here is local math over an ENU-ish meter frame
// projected onto lon/lat around a fixed center point.

export type Point = [number, number]; // [lon, lat]

const METERS_PER_LAT = 111_320;

export function metersPerLon(refLat: number): number {
  return 111_320 * Math.cos((refLat * Math.PI) / 180);
}

export function localPoint(centerLat: number, centerLon: number, northM: number, eastM: number): Point {
  return [centerLon + eastM / metersPerLon(centerLat), centerLat + northM / METERS_PER_LAT];
}

/** Point at compass bearing (deg, clockwise from north) and distance (m) from
 * center, with an optional lateral offset (m, positive = to the right of
 * someone facing along the bearing). */
export function projectPoint(centerLat: number, centerLon: number, bearingDeg: number, distanceM: number, lateralM = 0): Point {
  const rad = (bearingDeg * Math.PI) / 180;
  const north = distanceM * Math.cos(rad) - lateralM * Math.sin(rad);
  const east = distanceM * Math.sin(rad) + lateralM * Math.cos(rad);
  return localPoint(centerLat, centerLon, north, east);
}

export function distanceMeters(a: Point, b: Point, refLat: number): number {
  const dEast = (b[0] - a[0]) * metersPerLon(refLat);
  const dNorth = (b[1] - a[1]) * METERS_PER_LAT;
  return Math.hypot(dEast, dNorth);
}

export function bezierArc(p0: Point, ctrl: Point, p1: Point, steps = 8): Point[] {
  const pts: Point[] = [];
  for (let s = 0; s <= steps; s++) {
    const t = s / steps;
    const mt = 1 - t;
    pts.push([
      mt * mt * p0[0] + 2 * mt * t * ctrl[0] + t * t * p1[0],
      mt * mt * p0[1] + 2 * mt * t * ctrl[1] + t * t * p1[1],
    ]);
  }
  return pts;
}

/** Fraction (0..1) of a path's total length reached by the time it gets to
 * `points[index]` — used to mark a fixed stop-line point on a route whose
 * geometry (through vs. curved turn) varies in length. */
export function fractionAtIndex(points: Point[], index: number, refLat: number): number {
  let total = 0;
  let upTo = 0;
  for (let i = 0; i < points.length - 1; i++) {
    const len = distanceMeters(points[i], points[i + 1], refLat);
    total += len;
    if (i < index) upTo += len;
  }
  return total > 0 ? upTo / total : 0;
}

export interface SampledPath {
  totalLength: number;
  pointAt(fraction: number): Point;
  headingAt(fraction: number): number;
}

export function samplePath(points: Point[], refLat: number): SampledPath {
  const segLens: number[] = [];
  let total = 0;
  for (let i = 0; i < points.length - 1; i++) {
    const len = distanceMeters(points[i], points[i + 1], refLat);
    segLens.push(len);
    total += len;
  }
  const locate = (fraction: number): { i: number; t: number } => {
    const target = Math.max(0, Math.min(1, fraction)) * total;
    let acc = 0;
    for (let i = 0; i < segLens.length; i++) {
      const segLen = segLens[i];
      if (acc + segLen >= target || i === segLens.length - 1) {
        const segT = segLen > 0 ? (target - acc) / segLen : 0;
        return { i, t: Math.max(0, Math.min(1, segT)) };
      }
      acc += segLen;
    }
    return { i: Math.max(0, segLens.length - 1), t: 1 };
  };
  return {
    totalLength: total,
    pointAt(fraction) {
      const { i, t } = locate(fraction);
      const a = points[i];
      const b = points[i + 1] ?? points[i];
      return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t];
    },
    headingAt(fraction) {
      const { i } = locate(fraction);
      const a = points[i];
      const b = points[i + 1] ?? points[Math.max(0, i - 1)];
      const dEast = (b[0] - a[0]) * metersPerLon(refLat);
      const dNorth = (b[1] - a[1]) * METERS_PER_LAT;
      return ((Math.atan2(dEast, dNorth) * 180) / Math.PI + 360) % 360;
    },
  };
}

/** Full-circle ring polyline (closed) for roundabout pavement / islands. */
export function circle(centerLat: number, centerLon: number, radiusM: number, steps = 32): Point[] {
  const pts: Point[] = [];
  for (let i = 0; i <= steps; i++) {
    const bearing = (360 * i) / steps;
    pts.push(projectPoint(centerLat, centerLon, bearing, radiusM));
  }
  return pts;
}
