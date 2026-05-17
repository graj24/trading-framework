export type LiveEvent =
  | { type: "ltp_update"; symbol: string; price: number; change_pct: number }
  | { type: "trade_opened"; trade: Record<string, unknown> }
  | { type: "trade_closed"; trade: Record<string, unknown> }
  | { type: "pnl_update"; total_pnl_inr: number; total_pnl_pct: number }
  | { type: "alert"; message: string; severity: "info" | "warn" | "error" }
  | { type: "regime_change"; regime: string; confidence: number }
  | { type: "connected"; timestamp: string; message: string }
  | { type: "pong" };

type Handler = (event: LiveEvent) => void;

const _proto = window.location.protocol === "https:" ? "wss:" : "ws:";
const WS_URL = import.meta.env.VITE_WS_URL || `${_proto}//${window.location.host}/ws/live`;

class WSClient {
  private ws: WebSocket | null = null;
  private handlers: Set<Handler> = new Set();
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private shouldConnect = false;

  connect() {
    this.shouldConnect = true;
    this._connect();
  }

  disconnect() {
    this.shouldConnect = false;
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    this.ws?.close();
    this.ws = null;
  }

  subscribe(handler: Handler) {
    this.handlers.add(handler);
    return () => this.handlers.delete(handler);
  }

  send(msg: Record<string, unknown>) {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(msg));
    }
  }

  private _connect() {
    if (!this.shouldConnect) return;
    try {
      this.ws = new WebSocket(WS_URL);
      this.ws.onmessage = (e) => {
        try {
          const event = JSON.parse(e.data) as LiveEvent;
          this.handlers.forEach((h) => h(event));
        } catch {}
      };
      this.ws.onclose = () => {
        if (this.shouldConnect) {
          this.reconnectTimer = setTimeout(() => this._connect(), 3000);
        }
      };
      this.ws.onerror = () => this.ws?.close();
    } catch {}
  }
}

export const wsClient = new WSClient();
