import type { TrafficSignalState, VehicleState } from '../types/canonical';
import { localPoint, projectPoint, type Point } from './geometry';
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
  destinationRoads: DestinationRoad[];
}

export type DestinationRoadFeature = 'AIRPORT_CORRIDOR' | 'CITY_RAIL' | 'BUS_TERMINAL' | 'MARKET_ACCESS';

export interface DestinationRoad {
  id: string;
  label: string;
  feature: DestinationRoadFeature;
  path: Point[];
}

export const NETWORK_LAYOUT: Array<{ type: JunctionType; northM: number; eastM: number; id: string }> = [
  { type: 'CROSS', northM: 0, eastM: 0, id: 'hub' },
  { type: 'ROUNDABOUT', northM: 0, eastM: 440, id: 'roundabout' },
  { type: 'RAILWAY_CROSSING', northM: 0, eastM: -440, id: 'rail-crossing' },
  { type: 'T_JUNCTION', northM: -440, eastM: 0, id: 'south-t' },
];

// A traffic network is not uniformly loaded. This adapter-level demand
// profile keeps the central signalised approach visibly busy in the demo while
// leaving capacity on the branches for rerouting to use.
const NETWORK_DEMAND_SHARE = [0.55, 0.2, 0.15, 0.1];

function demandAllocation(total: number): number[] {
  const allocation = NETWORK_DEMAND_SHARE.map((share) => Math.floor(total * share));
  let remainder = Math.max(0, total - allocation.reduce((sum, value) => sum + value, 0));
  for (let index = 0; remainder > 0; index = (index + 1) % allocation.length, remainder -= 1) {
    allocation[index] += 1;
  }
  return allocation;
}

export function buildJunctionNetwork(lat: number, lon: number): JunctionNetwork {
  const destinationRoads: DestinationRoad[] = [
    { id: 'destination-airport', label: 'Kempegowda International Airport', feature: 'AIRPORT_CORRIDOR', path: [projectPoint(lat, lon, 0, 220), projectPoint(lat, lon, 0, 520)] },
    { id: 'destination-rail', label: 'KSR Bengaluru City Railway Station', feature: 'CITY_RAIL', path: [projectPoint(lat, lon, 270, 220), projectPoint(lat, lon, 270, 520)] },
    { id: 'destination-bus', label: 'Majestic / BMTC Bus Terminal', feature: 'BUS_TERMINAL', path: [projectPoint(lat, lon, 90, 220), projectPoint(lat, lon, 90, 520)] },
    { id: 'destination-market', label: 'KR Market', feature: 'MARKET_ACCESS', path: [projectPoint(lat, lon, 180, 220), projectPoint(lat, lon, 180, 520)] },
  ];
  return {
    center: [lon, lat],
    junctions: NETWORK_LAYOUT.map(({ type, northM, eastM }) => {
      const point = localPoint(lat, lon, northM, eastM);
      return buildJunction(type, point[1], point[0]);
    }),
    destinationRoads,
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
    const allocation = demandAllocation(vehicleCount);
    this.engines.forEach((engine, index) => engine.setVehicleCount(allocation[index] ?? 0));
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

  tick(dtMs: number, pedestrianPositions: Point[] = []): { vehicles: VehicleState[]; signals: TrafficSignalState[]; incidents: SimulationIncident[]; despawnedActorIds: string[] } {
    const frames = this.engines.map((engine) => {
      engine.setExternalPedestrians(() => pedestrianPositions);
      return engine.tick(dtMs);
    });
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
    const allocation = demandAllocation(vehicleCount);
    this.engines = this.network.junctions.map((junction, index) => new JunctionSimEngine({
      junction,
      vehicleCount: allocation[index] ?? 0,
      chaos: this.chaos,
      actorIdPrefix: `network-${NETWORK_LAYOUT[index].id}`,
      junctionId: `network-${NETWORK_LAYOUT[index].id}`,
      // The shared hub starts with a realistic directional demand imbalance:
      // the north approach is busy while the east approach is light. This is
      // an adapter-level input profile so the RL controller has something
      // meaningful to observe; congestion-aware routing can still redistribute
      // vehicles when the busy approach fills up.
      routeDemandWeights: index === 0
        ? Object.fromEntries(junction.routes.map((route) => [route.id, route.id.startsWith('N-') ? 6 : route.id.startsWith('E-') ? 1 : 2]))
        : undefined,
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
