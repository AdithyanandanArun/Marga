import { useWorldStore } from '../state/worldStore';
import { useAlertStore } from '../state/alertStore';
import type { WorldDelta, AlertStreamMessage, StreamMessage } from '../types/events';
import type { Alert, ConnectivityState, SystemMetrics } from '../types/canonical';

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
  // Per-channel timers and backoff. A single shared pair let the alert socket
  // overwrite the world socket's pending handle — so `disconnect` could only
  // ever cancel one of them — and let one channel's inflated backoff delay the
  // other's first reconnect, which is why the world feed sometimes took tens
  // of seconds to appear.
  private reconnectTimers: Record<'world' | 'alert', ReturnType<typeof setTimeout> | null> = { world: null, alert: null };
  private currentDelay: Record<'world' | 'alert', number>;
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
    this.currentDelay = { world: this.config.reconnectDelay, alert: this.config.reconnectDelay };
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
      this.currentDelay.world = this.config.reconnectDelay;
      if (this.disposed) return;
      this.config.onConnectionChange(true);
    };

    this.worldWs.onmessage = (event) => {
      try {
        const msg: StreamMessage = JSON.parse(event.data);
        if ('upserts' in msg) {
          useWorldStore.getState().applyDelta(msg as WorldDelta);
          const connectivity = (msg as WorldDelta & { connectivity?: { mode?: ConnectivityState } | null }).connectivity;
          if (connectivity?.mode) useWorldStore.getState().setConnectivity(connectivity.mode);
        } else if ('metrics' in msg) {
          useWorldStore.getState().updateMetrics((msg as { metrics: SystemMetrics }).metrics);
        }
      } catch {
        // malformed message
      }
    };

    this.worldWs.onclose = () => {
      // A disposed stream is not allowed to report. Closing a socket that is
      // still CONNECTING fires `onclose` asynchronously, so a stream torn down
      // on remount would otherwise flip the badge to "reconnecting" after its
      // replacement had already connected, and leave it stuck there.
      if (this.disposed) return;
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

    this.alertWs.onopen = () => { this.currentDelay.alert = this.config.reconnectDelay; };

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
    // Never stack attempts for the same channel; a flapping socket would
    // otherwise queue one timer per close and reconnect in a storm.
    if (this.reconnectTimers[which]) return;
    this.reconnectTimers[which] = setTimeout(() => {
      this.reconnectTimers[which] = null;
      if (which === 'world') this.connectWorld();
      else this.connectAlerts();
    }, this.currentDelay[which]);
    this.currentDelay[which] = Math.min(this.currentDelay[which] * 1.5, this.config.maxReconnectDelay);
  }

  disconnect(): void {
    this.disposed = true;
    for (const which of ['world', 'alert'] as const) {
      const timer = this.reconnectTimers[which];
      if (timer) clearTimeout(timer);
      this.reconnectTimers[which] = null;
    }
    this.worldWs?.close();
    this.alertWs?.close();
    this.worldWs = null;
    this.alertWs = null;
  }
}
