import type { TrafficSignalState, VehicleState } from '../types/canonical';
import { localPoint, type Point } from './geometry';
import { buildJunction, type JunctionDefinition, type JunctionType } from './junctionDefs';
import { JunctionSimEngine, type SignalTopologyPayload, type SimulationIncident } from './vehicleEngine';
import type { BodyPose } from './vehicleBody';

/** Two junctions can only share tarmac when their centres are within roughly
 * two arm lengths; anything further cannot produce a body conflict. */
const NEIGHBOUR_REACH_M = 500;

/** A small connected road district. Each branch joins the central cross at
 * its road endpoints, so this is one navigable visual network, not a grid of
 * unrelated examples. */
export interface JunctionNetwork {
  center: Point;
  junctions: JunctionDefinition[];
}

export const NETWORK_LAYOUT: Array<{ type: JunctionType; northM: number; eastM: number; id: string }> = [
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

  /** Topologies for every signalised junction, for RL controller registration. */
  signalTopologies(): SignalTopologyPayload[] {
    return this.engines
      .map((engine) => engine.signalTopology())
      .filter((topology): topology is SignalTopologyPayload => topology !== null);
  }

  /**
   * Apply one safety-approved RL action addressed by signal_id.
   * Returns false when no junction owns that signal, so an unroutable command
   * is reported rather than silently discarded.
   */
  applySignalAction(signalId: string, action: string): boolean {
    const engine = this.engines.find((candidate) => candidate.signalTopology()?.signal_id === signalId);
    return engine ? engine.applySignalAction(action) : false;
  }

  tick(dtMs: number): { vehicles: VehicleState[]; signals: TrafficSignalState[]; incidents: SimulationIncident[]; despawnedActorIds: string[] } {
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
    return frames.reduce<{ vehicles: VehicleState[]; signals: TrafficSignalState[]; incidents: SimulationIncident[]; despawnedActorIds: string[] }>(
      (frame, next) => {
        frame.vehicles.push(...next.vehicles);
        frame.signals.push(...next.signals);
        frame.incidents.push(...next.incidents);
        frame.despawnedActorIds.push(...next.despawnedActorIds);
        return frame;
      }, { vehicles: [], signals: [], incidents: [], despawnedActorIds: [] },
    );
  }

  /** Bodies belonging to every other junction engine. Only neighbours whose
   * road network can actually touch this one are considered, so the seam check
   * stays cheap as the district grows. */
  private neighbourBodies(engine: JunctionSimEngine): BodyPose[] {
    const bodies: BodyPose[] = [];
    const own = this.centreOf(engine);
    for (const other of this.engines) {
      if (other === engine) continue;
      const centre = this.centreOf(other);
      const spacingM = Math.hypot(
        (centre[0] - own[0]) * 111_320 * Math.cos(own[1] * Math.PI / 180),
        (centre[1] - own[1]) * 111_320,
      );
      if (spacingM > NEIGHBOUR_REACH_M) continue;
      bodies.push(...other.bodies());
    }
    return bodies;
  }

  private centreOf(engine: JunctionSimEngine): Point {
    const index = this.engines.indexOf(engine);
    return this.network.junctions[index].center;
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
    // Adjacent junctions share an arm tip exactly (220 m arms, 440 m spacing),
    // so vehicles from two engines can occupy the same tarmac at a handover
    // point. Without this each engine only sees its own vehicles and lets them
    // pass through each other at the seam.
    for (const engine of this.engines) {
      engine.setExternalBodies(() => this.neighbourBodies(engine));
    }
  }
}
