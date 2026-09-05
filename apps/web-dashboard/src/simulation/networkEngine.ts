import type { TrafficSignalState, VehicleState } from '../types/canonical';
import { localPoint, type Point } from './geometry';
import { buildJunction, type JunctionDefinition, type JunctionType } from './junctionDefs';
import { JunctionSimEngine } from './vehicleEngine';

/** A small connected road district. Each branch joins the central cross at
 * its road endpoints, so this is one navigable visual network, not a grid of
 * unrelated examples. */
export interface JunctionNetwork {
  center: Point;
  junctions: JunctionDefinition[];
}

const NETWORK_LAYOUT: Array<{ type: JunctionType; northM: number; eastM: number; id: string }> = [
  { type: 'CROSS', northM: 0, eastM: 0, id: 'hub' },
  { type: 'ROUNDABOUT', northM: 0, eastM: 440, id: 'roundabout' },
  { type: 'RAILWAY_CROSSING', northM: 0, eastM: -440, id: 'rail-crossing' },
  { type: 'T_JUNCTION', northM: -440, eastM: 0, id: 'south-t' },
];

export function buildJunctionNetwork(lat: number, lon: number): JunctionNetwork {
  return {
    center: [lon, lat],
    junctions: NETWORK_LAYOUT.map(({ type, northM, eastM }) => {
      const point = localPoint(lat, lon, northM, eastM);
      return buildJunction(type, point[1], point[0]);
    }),
  };
}

export class JunctionNetworkEngine {
  private network: JunctionNetwork;
  private engines: JunctionSimEngine[] = [];
  private chaos: number;

  constructor(network: JunctionNetwork, vehicleCount: number, chaos: number) {
    this.network = network;
    this.chaos = chaos;
    this.rebuild(vehicleCount);
  }

  setVehicleCount(vehicleCount: number): void {
    const base = Math.floor(vehicleCount / this.engines.length);
    const remainder = vehicleCount % this.engines.length;
    this.engines.forEach((engine, index) => engine.setVehicleCount(base + (index < remainder ? 1 : 0)));
  }

  setChaos(chaos: number): void {
    this.chaos = chaos;
    this.engines.forEach((engine) => engine.setChaos(chaos));
  }

  reset(vehicleCount: number): void {
    this.rebuild(vehicleCount);
  }

  tick(dtMs: number): { vehicles: VehicleState[]; signals: TrafficSignalState[] } {
    const frames = this.engines.map((engine) => engine.tick(dtMs));
    for (const [sourceIndex, frame] of frames.entries()) {
      for (const exiting of frame.exits) {
        const recipients = this.engines
          .map((engine, index) => ({ engine, index }))
          .filter(({ engine, index }) => index !== sourceIndex && engine.canAcceptAt(exiting.position));
        const recipient = recipients[0]?.engine;
        // A handoff keeps the actor ID and position continuous. At busy
        // downstream junctions the engine chooses its least-loaded legal exit;
        // otherwise it samples a route for natural mixed-traffic variation.
        if (recipient?.acceptTransfer(exiting, exiting.position, recipient.isCongested())) continue;
        this.engines[sourceIndex].respawn(exiting);
      }
    }
    return frames.reduce<{ vehicles: VehicleState[]; signals: TrafficSignalState[] }>(
      (frame, next) => {
        frame.vehicles.push(...next.vehicles);
        frame.signals.push(...next.signals);
        return frame;
      }, { vehicles: [], signals: [] },
    );
  }

  private rebuild(vehicleCount: number): void {
    const base = Math.floor(vehicleCount / this.network.junctions.length);
    const remainder = vehicleCount % this.network.junctions.length;
    this.engines = this.network.junctions.map((junction, index) => new JunctionSimEngine({
      junction,
      vehicleCount: base + (index < remainder ? 1 : 0),
      chaos: this.chaos,
      actorIdPrefix: `network-${NETWORK_LAYOUT[index].id}`,
      junctionId: `network-${NETWORK_LAYOUT[index].id}`,
    }));
  }
}
