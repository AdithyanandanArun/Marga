// Shared reconnect-with-backoff WebSocket wrapper. Every new-contract stream
// client (graph, routes, v2x) needs the same behavior WorldStream already
// has for world-state/alerts, so it lives here once instead of four times.

export interface ReconnectingSocketOptions {
  url: string;
  onMessage: (data: string) => void;
  onConnectionChange?: (connected: boolean) => void;
  reconnectDelay?: number;
  maxReconnectDelay?: number;
}

export class ReconnectingSocket {
  private ws: WebSocket | null = null;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private currentDelay: number;
  private disposed = false;
  private readonly opts: Required<Omit<ReconnectingSocketOptions, 'onConnectionChange'>> & Pick<ReconnectingSocketOptions, 'onConnectionChange'>;

  constructor(opts: ReconnectingSocketOptions) {
    this.opts = {
      reconnectDelay: 1000,
      maxReconnectDelay: 30000,
      ...opts,
    };
    this.currentDelay = this.opts.reconnectDelay;
  }

  connect(): void {
    if (this.disposed) return;
    try {
      this.ws = new WebSocket(this.opts.url);
    } catch {
      this.scheduleReconnect();
      return;
    }
    this.ws.onopen = () => {
      this.currentDelay = this.opts.reconnectDelay;
      this.opts.onConnectionChange?.(true);
    };
    this.ws.onmessage = (event) => this.opts.onMessage(event.data);
    this.ws.onclose = () => {
      this.opts.onConnectionChange?.(false);
      this.scheduleReconnect();
    };
    this.ws.onerror = () => this.ws?.close();
  }

  private scheduleReconnect(): void {
    if (this.disposed) return;
    this.reconnectTimer = setTimeout(() => this.connect(), this.currentDelay);
    this.currentDelay = Math.min(this.currentDelay * 1.5, this.opts.maxReconnectDelay);
  }

  disconnect(): void {
    this.disposed = true;
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    this.ws?.close();
    this.ws = null;
  }
}
