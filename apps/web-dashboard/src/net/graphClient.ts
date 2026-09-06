import type { GraphEdgeMetrics, GraphIntersection, GraphStreamMessage } from '../types/graph';
import { useGraphStore } from '../state/graphStore';
import { ReconnectingSocket } from './reconnectingSocket';

// services/mobility_graph/api.py mounts this router at "/graph" directly
// (no /v1 prefix — see gateway/app.py's _try_mount_router call for it), so
// these calls bypass the /v1 proxy convention every other route here uses.
// vite.config.ts proxies /graph to the gateway to match.

export async function fetchGraphEdge(edgeId: string): Promise<GraphEdgeMetrics | null> {
  try {
    const res = await fetch(`/graph/edges/${encodeURIComponent(edgeId)}`);
    if (!res.ok) return null;
    return (await res.json()) as GraphEdgeMetrics;
  } catch {
    return null;
  }
}

export async function fetchGraphIntersection(intersectionId: string): Promise<GraphIntersection | null> {
  try {
    const res = await fetch(`/graph/intersections/${encodeURIComponent(intersectionId)}`);
    if (!res.ok) return null;
    return (await res.json()) as GraphIntersection;
  } catch {
    return null;
  }
}

export class GraphStream {
  private socket: ReconnectingSocket;

  constructor() {
    const wsBase = `${window.location.protocol === 'https:' ? 'wss' : 'ws'}://${window.location.host}`;
    this.socket = new ReconnectingSocket({
      url: `${wsBase}/graph/stream`,
      onMessage: (data) => this.handleMessage(data),
      onConnectionChange: (connected) => useGraphStore.getState().setConnected(connected),
    });
  }

  connect(): void {
    this.socket.connect();
  }

  disconnect(): void {
    this.socket.disconnect();
    useGraphStore.getState().setConnected(false);
  }

  private handleMessage(data: string): void {
    try {
      const msg: GraphStreamMessage = JSON.parse(data);
      if (msg.event_type === 'graph.edge.updated') useGraphStore.getState().upsertEdge(msg.data as unknown as GraphEdgeMetrics);
      else if (msg.event_type === 'graph.intersection.updated') useGraphStore.getState().upsertIntersection(msg.data as unknown as GraphIntersection);
    } catch {
      // graph service sent garbage or isn't ready — never fabricate a substitute.
    }
  }
}
