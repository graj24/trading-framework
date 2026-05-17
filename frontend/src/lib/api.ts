const BASE = import.meta.env.VITE_API_BASE_URL || "";

async function req<T>(path: string, opts?: RequestInit): Promise<T> {
  const r = await fetch(BASE + path, opts);
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  return r.json();
}

export const api = {
  trades: (params?: Record<string, string>) => {
    const q = params ? "?" + new URLSearchParams(params) : "";
    return req<Trade[]>(`/api/trades${q}`);
  },
  trade: (id: number) => req<Trade>(`/api/trades/${id}`),
  signal: (symbol: string) => req<SignalScores>(`/api/signals/${symbol}`),
  runSignal: (symbol: string) =>
    req<{ status: string }>(`/api/signals/run?symbol=${symbol}`, { method: "POST" }),
  agentStatus: () => req<AgentStatus[]>("/api/agents/status"),
  regime: () => req<Record<string, unknown>>("/api/market/regime"),
  sectors: () => req<Record<string, number | null>>("/api/market/sectors"),
  ltp: (symbol: string) => req<LtpData>(`/api/market/ltp/${symbol}`),
  candles: (symbol: string, interval = "1d") =>
    req<Candle[]>(`/api/candles/${symbol}?interval=${interval}`),
  config: () => req<Record<string, unknown>>("/api/config"),
  patchConfig: (updates: Record<string, unknown>) =>
    req<{ status: string }>("/api/config", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(updates),
    }),
  envStatus: () => req<Record<string, string>>("/api/env/status"),
  patchEnv: (updates: Record<string, string>) =>
    req<{ status: string }>("/api/env", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(updates),
    }),
  testService: (service: string) =>
    req<{ status: string; error?: string }>(`/api/env/test/${service}`, { method: "POST" }),
  infra: () => req<InfraStatus>("/api/infra"),
};

// Types
export interface Trade {
  id: string | number;
  symbol: string;
  entry_date?: string;
  entry_price?: number;
  stop_loss?: number;
  target?: number;
  position_size?: number;
  exit_date?: string;
  exit_price?: number;
  pnl_pct?: number;
  pnl_inr?: number;
  outcome?: string;
  reasoning?: string;
  technical_score?: number;
  sentiment?: number;
  pattern_ev?: number;
  signal_source?: string;
}

export interface SignalScores {
  symbol: string;
  price?: number;
  technical_score?: number;
  sentiment?: number;
  pattern_ev?: number;
  ml_signal?: unknown;
  ml_proba?: unknown;
  regime?: string;
  decision?: string;
  confidence?: number;
  reasoning?: string;
}

export interface AgentStatus {
  name: string;
  status: string;
  last_run?: string;
  last_score?: number;
}

export interface LtpData {
  symbol: string;
  price?: number;
  change_pct?: number;
}

export interface InfraStatus {
  timestamp: string;
  instance: { id: string; type: string; public_ip: string; region: string };
  system: {
    uptime: string;
    load: string[];
    disk: { total: string; used: string; free: string; pct: string };
    memory: { total_mb: number; used_mb: number; free_mb: number };
  };
  services: Record<string, string>;
  multica: { status: string; agents: string; workspaces: string };
  deploy: { branch: string; commit: string };
}

export interface Candle {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

// Streaming backtest helper
export async function* streamBacktest(
  endpoint: string,
  body: Record<string, unknown>
): AsyncGenerator<Record<string, unknown>> {
  const r = await fetch(BASE + endpoint, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.body) return;
  const reader = r.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    const lines = buf.split("\n");
    buf = lines.pop() ?? "";
    for (const line of lines) {
      if (line.trim()) yield JSON.parse(line);
    }
  }
}
