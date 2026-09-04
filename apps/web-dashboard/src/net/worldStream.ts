import { useWorldStore } from '../state/worldStore';
import { useAlertStore } from '../state/alertStore';
import type { WorldDelta, AlertStreamMessage, StreamMessage } from '../types/events';
import type { Alert, SystemMetrics } from '../types/canonical';

interface StreamConfig {
  worldUrl?: string;
  alertUrl?: string;
  bbox?: [number, number, number, number];
  detail?: number;
  reconnectDelay?: number;
  maxReconnectDelay?: number;
  onConnectionChange?: (connected: boolean) => void;
}

export class WorldStream {
  private worldWs: WebSocket | null = null;
  private alertWs: WebSocket | null = null;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private currentDelay: number;
  private config: Required<StreamConfig>;
  private disposed = false;

  constructor(config: StreamConfig = {}) {
    const wsBase = `ws://${window.location.host}`;
    this.config = {
      worldUrl: config.worldUrl ?? `${wsBase}/v1/world-state/stream`,
      alertUrl: config.alertUrl ?? `${wsBase}/v1/stream/alerts`,
      bbox: config.bbox ?? [77.5, 12.9, 77.7, 13.1],
      detail: config.detail ?? 2,
      reconnectDelay: config.reconnectDelay ?? 1000,
      maxReconnectDelay: config.maxReconnectDelay ?? 30000,
      onConnectionChange: config.onConnectionChange ?? (() => {}),
    };
    this.currentDelay = this.config.reconnectDelay;
  }

  connect(): void {
    this.connectWorld();
    this.connectAlerts();
  }

  private connectWorld(): void {
    if (this.disposed) return;

    const [minLon, minLat, maxLon, maxLat] = this.config.bbox;
    const url = `${this.config.worldUrl}?bbox=${minLon},${minLat},${maxLon},${maxLat}&detail=${this.config.detail}`;

    try {
      this.worldWs = new WebSocket(url);
    } catch {
      this.scheduleReconnect('world');
      return;
    }

    this.worldWs.onopen = () => {
      this.currentDelay = this.config.reconnectDelay;
      this.config.onConnectionChange(true);
    };

    this.worldWs.onmessage = (event) => {
      try {
        const msg: StreamMessage = JSON.parse(event.data);
        if ('upserts' in msg) {
          useWorldStore.getState().applyDelta(msg as WorldDelta);
        } else if ('metrics' in msg) {
          useWorldStore.getState().updateMetrics((msg as { metrics: SystemMetrics }).metrics);
        }
      } catch {
        // malformed message
      }
    };

    this.worldWs.onclose = () => {
      this.config.onConnectionChange(false);
      this.scheduleReconnect('world');
    };

    this.worldWs.onerror = () => {
      this.worldWs?.close();
    };
  }

  private connectAlerts(): void {
    if (this.disposed) return;

    try {
      this.alertWs = new WebSocket(this.config.alertUrl);
    } catch {
      this.scheduleReconnect('alert');
      return;
    }

    this.alertWs.onmessage = (event) => {
      try {
        const msg: AlertStreamMessage = JSON.parse(event.data);
        if (msg.kind === 'alert') {
          useAlertStore.getState().upsertAlert(msg.alert as Alert);
        }
      } catch {
        // malformed message
      }
    };

    this.alertWs.onclose = () => {
      this.scheduleReconnect('alert');
    };

    this.alertWs.onerror = () => {
      this.alertWs?.close();
    };
  }

  updateBbox(bbox: [number, number, number, number]): void {
    this.config.bbox = bbox;
    if (this.worldWs?.readyState === WebSocket.OPEN) {
      this.worldWs.send(JSON.stringify({ type: 'viewport', bbox }));
    }
  }

  private scheduleReconnect(which: 'world' | 'alert'): void {
    if (this.disposed) return;
    this.reconnectTimer = setTimeout(() => {
      if (which === 'world') this.connectWorld();
      else this.connectAlerts();
    }, this.currentDelay);
    this.currentDelay = Math.min(this.currentDelay * 1.5, this.config.maxReconnectDelay);
  }

  disconnect(): void {
    this.disposed = true;
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    this.worldWs?.close();
    this.alertWs?.close();
    this.worldWs = null;
    this.alertWs = null;
  }
}
