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
  pms: () => req<PMSummary[]>("/api/pms"),
  pmState: (id: string) => req<PMState>(`/api/pms/${id}/state`),
  pmAudit: (id: string) => req<AuditEntry[]>(`/api/pms/${id}/audit`),
  pmTriageLog: (id: string) => req<TriageDecision[]>(`/api/pms/${id}/triage_log`),
  pmTrades: (id: string) => req<Trade[]>(`/api/pms/${id}/trades`),
  pmEquityToday: (id: string) => req<EquityPoint[]>(`/api/pms/${id}/equity_today`),
  pmEvents: (sinceId: number, pmId?: string) => {
    const q = new URLSearchParams({ since_id: String(sinceId) });
    if (pmId) q.set("pm_id", pmId);
    return req<PMEvent[]>(`/api/pms/events?${q}`);
  },
  killSwitchStatus: () => req<{ active: boolean; reason: string }>("/api/pms/kill_switch"),
  killSwitchOn: (reason?: string) =>
    req<{ active: boolean }>(`/api/pms/kill_switch/activate?reason=${encodeURIComponent(reason ?? "manual via UI")}`, { method: "POST" }),
  killSwitchOff: () =>
    req<{ active: boolean }>("/api/pms/kill_switch/deactivate", { method: "POST" }),
  pmPause: (id: string, reason?: string) =>
    req<{ paused: boolean }>(`/api/pms/${id}/pause?reason=${encodeURIComponent(reason ?? "manual via UI")}`, { method: "POST" }),
  pmResume: (id: string) =>
    req<{ paused: boolean }>(`/api/pms/${id}/resume`, { method: "POST" }),
  pmPausedStatus: (id: string) =>
    req<{ paused: boolean; reason: string }>(`/api/pms/${id}/paused`),
  leaderboard: (windowDays = 30) =>
    req<LeaderboardEntry[]>(`/api/pms/leaderboard?window_days=${windowDays}`),
  pmStrategies: (id: string) =>
    req<{ pm_id: string; active_version: number | null; versions: StrategyVersion[] }>(`/api/pms/${id}/strategies`),
  pmStrategyDiff: (id: string, vA: number, vB: number) =>
    req<{ diff: string }>(`/api/pms/${id}/strategies/diff?v_a=${vA}&v_b=${vB}`),
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

// ── PM types ──────────────────────────────────────────────────────────────────

export interface PMSummary {
  pm_id: string;
  active: boolean;
  daily_pnl_inr: number;
  open_positions: number;
  inbox_count: number;
  last_wakeup: string | null;
  capital: number;
  daemons: Record<string, { ts: string; event: string }>;
}

export interface PMState {
  pm_id: string;
  plan: string;
  tasks: Record<string, unknown[]>;
  journal: string;
  journal_summary: string;
  inbox: unknown[];
  positions: unknown[];
  proposals: unknown[];
  team: Record<string, unknown>;
}

export interface PMEvent {
  id: number;
  topic: string;
  payload: Record<string, unknown>;
  pm_id: string | null;
  severity: string;
  ts: string;
}

export interface EquityPoint {
  ts: string;
  symbol: string;
  pnl: number;
  cum_pnl: number;
  exit_reason: string;
}

export interface AuditEntry {
  ts: string;
  pm_id: string;
  event: string;
  [key: string]: unknown;
}

export interface TriageDecision {
  ts: string;
  topic: string;
  symbol: string;
  classification: string;
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

export interface LeaderboardEntry {
  pm_id: string;
  total_pnl: number;
  n_trades: number;
  win_rate_pct: number;
  sharpe: number;
  max_drawdown_inr: number;
  open_positions: number;
  window_days: number;
}

export interface StrategyVersion {
  version: number;
  created_at: string;
  notes: string;
  parent_version: number | null;
  file: string;
}
