import type { NodeConnectivity, NodeNeighbour, RiskCreated, V2XMessage } from '../types/v2x';
import { useV2XStore } from '../state/v2xStore';
import { ReconnectingSocket } from './reconnectingSocket';

// Per final_imp.md:
//   WS  v2x.message
//   WS  risk.created
//   GET /nodes/:id/neighbours
//   GET /nodes/:id/connectivity
// Namespaced under /v1 to match every other gateway route in this app.

export async function fetchNodeNeighbours(nodeId: string): Promise<NodeNeighbour[] | null> {
  try {
    const res = await fetch(`/v1/nodes/${encodeURIComponent(nodeId)}/neighbours`);
    if (!res.ok) return null;
    return (await res.json()) as NodeNeighbour[];
  } catch {
    return null;
  }
}

export async function fetchNodeConnectivity(nodeId: string): Promise<NodeConnectivity | null> {
  try {
    const res = await fetch(`/v1/nodes/${encodeURIComponent(nodeId)}/connectivity`);
    if (!res.ok) return null;
    return (await res.json()) as NodeConnectivity;
  } catch {
    return null;
  }
}

type V2XStreamMessage =
  | { type: 'v2x.message'; message: V2XMessage }
  | { type: 'risk.created'; risk: RiskCreated }
  | { type: 'node.connectivity'; connectivity: NodeConnectivity };

export class V2XStream {
  private socket: ReconnectingSocket;

  constructor() {
    const wsBase = `ws://${window.location.host}`;
    this.socket = new ReconnectingSocket({
      url: `${wsBase}/v1/stream/v2x`,
      onMessage: (data) => this.handleMessage(data),
      onConnectionChange: (connected) => useV2XStore.getState().setStreamConnected(connected),
    });
  }

  connect(): void {
    this.socket.connect();
  }

  disconnect(): void {
    this.socket.disconnect();
    useV2XStore.getState().setStreamConnected(false);
  }

  private handleMessage(data: string): void {
    try {
      const msg: V2XStreamMessage = JSON.parse(data);
      if (msg.type === 'v2x.message') useV2XStore.getState().recordMessage(msg.message);
      else if (msg.type === 'risk.created') useV2XStore.getState().recordRisk(msg.risk);
      else if (msg.type === 'node.connectivity') useV2XStore.getState().setNodeConnectivity(msg.connectivity);
    } catch {
      // edge V2X service not ready or sent garbage — never fabricate a link.
    }
  }
}
