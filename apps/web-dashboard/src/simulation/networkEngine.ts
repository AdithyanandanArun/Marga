import type { TrafficSignalState, VehicleState } from '../types/canonical';
import { distanceMeters, localPoint, type Point } from './geometry';
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
  /** A two-way diagonal bypass: east approach → south approach → west approach.
   * Its vertices are existing road midpoints, so it merges cleanly rather than
   * creating a floating destination road or expanding the camera bounds. */
  bypassRoads: Point[][];
  /** Centre markings make the 10 m bypass carriageway legible as two lanes. */
  bypassLaneMarkings: Point[][];
  /** Physical stop bars at the controlled crossing on the hub ↔ T corridor. */
  bypassStopLines: Point[][];
  controlledIntersections: ControlledIntersection[];
}

export interface ControlledIntersection {
  id: string;
  /** Two signal heads: the through road and the bypass movement. */
  groupAPosition: Point;
  groupBPosition: Point;
  greenMs: number;
  amberMs: number;
  allRedMs: number;
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
  return {
    center: [lon, lat],
    junctions: NETWORK_LAYOUT.map(({ type, northM, eastM }) => {
      const point = localPoint(lat, lon, northM, eastM);
      return buildJunction(type, point[1], point[0]);
    }),
    bypassRoads: [],
    bypassLaneMarkings: [],
    bypassStopLines: [],
    controlledIntersections: [],
  };
}

export class JunctionNetworkEngine {
  private network: JunctionNetwork;
  /** The four normal junction engines. The bypass is intentionally excluded
   * from demand allocation: it earns vehicles only through a route decision. */
  private primaryEngines: JunctionSimEngine[] = [];
  private bypassEngine: JunctionSimEngine | null = null;
  private engines: JunctionSimEngine[] = [];
  private engineCentres = new Map<JunctionSimEngine, Point>();
  private chaos: number;
  private clockMs = 0;

  constructor(network: JunctionNetwork, vehicleCount: number, chaos: number) {
    this.network = network;
    this.chaos = chaos;
    this.rebuild(vehicleCount);
  }

  setVehicleCount(vehicleCount: number): void {
    const allocation = demandAllocation(vehicleCount);
    this.primaryEngines.forEach((engine, index) => engine.setVehicleCount(allocation[index] ?? 0));
  }

  setChaos(chaos: number): void {
    this.chaos = chaos;
    this.engines.forEach((engine) => engine.setChaos(chaos));
  }

  reset(vehicleCount: number): void {
    this.clockMs = 0;
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
    this.clockMs += Math.max(0, dtMs);
    const frames = this.engines.map((engine) => {
      engine.setExternalPedestrians(() => pedestrianPositions);
      return engine.tick(dtMs);
    });
    for (const [sourceIndex, frame] of frames.entries()) {
      for (const exiting of frame.exits) {
        const recipients = this.engines
          .map((engine, index) => ({ engine, index }))
          .filter(({ engine, index }) => index !== sourceIndex && engine.canAcceptAt(exiting.position));
        const recipient = this.chooseRecipient(this.engines[sourceIndex], recipients, exiting.position);
        // A handoff keeps the actor ID and position continuous. At a branch
        // with a real alternative, congestion at the hub selects the bypass;
        // otherwise the original corridor remains preferred.
        if (recipient?.acceptTransfer(exiting, exiting.position, recipient.isCongested())) continue;
        this.engines[sourceIndex].respawn(exiting);
      }
    }
    const frame = frames.reduce<{ vehicles: VehicleState[]; signals: TrafficSignalState[]; incidents: SimulationIncident[]; despawnedActorIds: string[] }>(
      (frame, next) => {
        frame.vehicles.push(...next.vehicles);
        frame.signals.push(...next.signals);
        frame.incidents.push(...next.incidents);
        frame.despawnedActorIds.push(...next.despawnedActorIds);
        return frame;
      }, { vehicles: [], signals: [], incidents: [], despawnedActorIds: [] },
    );
    frame.signals.push(...this.controlledIntersectionSignals());
    return frame;
  }

  /** Emits the operational state of the bypass crossing.  The amber and
   * all-red intervals are explicit so the map never suggests conflicting
   * movements have green at the same time. */
  private controlledIntersectionSignals(): TrafficSignalState[] {
    const now = new Date().toISOString();
    return this.network.controlledIntersections.flatMap((intersection) => {
      const halfCycle = intersection.greenMs + intersection.amberMs + intersection.allRedMs;
      const cycle = halfCycle * 2;
      const t = this.clockMs % cycle;
      const inA = t < halfCycle;
      const withinPhase = t % halfCycle;
      const active = withinPhase < intersection.greenMs
        ? 'GREEN'
        : withinPhase < intersection.greenMs + intersection.amberMs
          ? 'AMBER'
          : 'RED';
      const inactive = 'RED';
      const groupAState = inA ? active : inactive;
      const groupBState = inA ? inactive : active;
      const remainingMs = active === 'GREEN'
        ? intersection.greenMs - withinPhase
        : active === 'AMBER'
          ? intersection.greenMs + intersection.amberMs - withinPhase
          : halfCycle - withinPhase;
      const state = (group: 'A' | 'B', phase: 'RED' | 'AMBER' | 'GREEN', position: Point): TrafficSignalState => ({
        signal_id: `${intersection.id}-signal-${group.toLowerCase()}`,
        junction_id: intersection.id,
        ts: now,
        phases: [{ movement_id: group, state: phase }],
        phase_remaining_s: Math.max(0, remainingMs / 1000),
        controller_mode: 'FIXED',
        source: 'SIMULATION',
        confidence: 1,
        position: { lon: position[0], lat: position[1] },
      });
      return [
        state('A', groupAState, intersection.groupAPosition),
        state('B', groupBState, intersection.groupBPosition),
      ];
    });
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
    return this.engineCentres.get(engine) ?? this.network.center;
  }

  private rebuild(vehicleCount: number): void {
    const allocation = demandAllocation(vehicleCount);
    this.primaryEngines = this.network.junctions.map((junction, index) => new JunctionSimEngine({
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
    this.bypassEngine = null;
    this.engines = [...this.primaryEngines];
    this.engineCentres = new Map(this.primaryEngines.map((engine, index) => [engine, this.network.junctions[index].center] as const));
    // Adjacent junctions share an arm tip exactly (220 m arms, 440 m spacing),
    // so vehicles from two engines can occupy the same tarmac at a handover
    // point. Without this each engine only sees its own vehicles and lets them
    // pass through each other at the seam.
    for (const engine of this.engines) {
      engine.setExternalBodies(() => this.neighbourBodies(engine));
    }
  }

  /** Pick the physical continuation at a shared endpoint.
   *
   * The bypass is deliberately not a random extra exit. It is eligible only
   * for traffic coming from the rail or roundabout toward the hub, and only
   * while the hub reports congestion. Once a vehicle enters the cut, it is
   * committed to the opposite corridor rather than being bounced back through
   * the hub at the next handoff.
   */
  private chooseRecipient(
    source: JunctionSimEngine,
    candidates: Array<{ engine: JunctionSimEngine; index: number }>,
    position: Point,
  ): JunctionSimEngine | undefined {
    const hub = this.primaryEngines[0];
    const roundabout = this.primaryEngines[1];
    const railway = this.primaryEngines[2];
    const bypass = this.bypassEngine;
    if (!hub || !bypass) return candidates[0]?.engine;

    if (source === bypass) {
      // The east and west ends converge at existing approaches. Select the
      // non-hub continuation so the bypass has a stable, visible destination.
      const eastEnd = this.network.bypassRoads[0][0];
      const target = distanceMeters(position, eastEnd, this.network.center[1]) < 2 ? roundabout : railway;
      return candidates.find(({ engine }) => engine === target)?.engine
        ?? candidates.find(({ engine }) => engine !== hub)?.engine
        ?? candidates[0]?.engine;
    }

    const cameFromOuterBranch = source === roundabout || source === railway;
    const canUseCut = candidates.some(({ engine }) => engine === bypass);
    if (cameFromOuterBranch && canUseCut && hub.isCongested() && !bypass.isCongested()) {
      return bypass;
    }

    // Retain the direct corridor while it has capacity. This hysteresis keeps
    // vehicles from oscillating onto a longer cut for marginal differences.
    return candidates.find(({ engine }) => engine !== bypass)?.engine ?? candidates[0]?.engine;
  }
}
