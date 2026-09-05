import type { ActorType } from '../types/canonical';
import { metersPerLon, type Point } from './geometry';

// Shared by rendering and physics: distances are bumper/body distances.
export function dimensions(type: ActorType) {
  return { length: type === 'BUS' ? 10 : type === 'TRUCK' ? 8 : type === 'BIKE' ? 2.2 : 4.6,
    width: type === 'BUS' ? 2.6 : type === 'BIKE' ? 0.9 : 1.9 };
}
export interface BodyPose { id: string; type: ActorType; position: Point; heading: number }
export function bodiesOverlap(a: BodyPose, b: BodyPose, margin = 0): boolean {
  const delta = [(b.position[0] - a.position[0]) * metersPerLon(a.position[1]),
    (b.position[1] - a.position[1]) * 111320];
  const axes = (heading: number) => {
    const r = heading * Math.PI / 180;
    return [[Math.sin(r), Math.cos(r)], [Math.cos(r), -Math.sin(r)]];
  };
  const aa = axes(a.heading), ba = axes(b.heading);
  const ad = dimensions(a.type), bd = dimensions(b.type);
  const dot = (u: number[], v: number[]) => u[0] * v[0] + u[1] * v[1];
  for (const axis of [...aa, ...ba]) {
    const radius = (ad.length / 2 + margin) * Math.abs(dot(aa[0], axis))
      + (ad.width / 2 + margin) * Math.abs(dot(aa[1], axis))
      + (bd.length / 2 + margin) * Math.abs(dot(ba[0], axis))
      + (bd.width / 2 + margin) * Math.abs(dot(ba[1], axis));
    if (Math.abs(dot(delta, axis)) >= radius) return false;
  }
  return true;
}
