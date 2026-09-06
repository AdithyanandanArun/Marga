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
    // A reconnect timer and an already-opening socket are both valid pending
    // connection attempts. Starting another one creates socket storms, which
    // is especially damaging when the gateway is briefly busy processing a
    // large traffic frame.
    if (this.ws?.readyState === WebSocket.OPEN || this.ws?.readyState === WebSocket.CONNECTING) return;
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    let socket: WebSocket;
    try {
      socket = new WebSocket(this.opts.url);
    } catch {
      this.scheduleReconnect();
      return;
    }
    this.ws = socket;
    socket.onopen = () => {
      if (this.disposed || this.ws !== socket) return;
      this.currentDelay = this.opts.reconnectDelay;
      this.opts.onConnectionChange?.(true);
    };
    socket.onmessage = (event) => {
      if (this.ws === socket && !this.disposed) this.opts.onMessage(event.data);
    };
    socket.onclose = () => {
      // Closing a superseded socket must not mark a newer live connection as
      // offline or schedule a duplicate reconnect.
      if (this.disposed || this.ws !== socket) return;
      this.ws = null;
      this.opts.onConnectionChange?.(false);
      this.scheduleReconnect();
    };
    socket.onerror = () => {
      if (this.ws === socket) socket.close();
    };
  }

  private scheduleReconnect(): void {
    if (this.disposed) return;
    if (this.reconnectTimer || this.ws?.readyState === WebSocket.OPEN || this.ws?.readyState === WebSocket.CONNECTING) return;
    this.reconnectTimer = setTimeout(() => this.connect(), this.currentDelay);
    this.currentDelay = Math.min(this.currentDelay * 1.5, this.opts.maxReconnectDelay);
  }

  disconnect(): void {
    this.disposed = true;
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    this.reconnectTimer = null;
    if (this.ws) {
      this.ws.onopen = null;
      this.ws.onmessage = null;
      this.ws.onclose = null;
      this.ws.onerror = null;
      this.ws.close();
    }
    this.ws = null;
  }
}
