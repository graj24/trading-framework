// Typed fetch helpers for the AGORA control plane.
//
// Shapes mirror src/agora/platform/control_plane/app.py exactly. If you change
// a Pydantic model there, change the matching type here.
//
// API base resolves at module load via NEXT_PUBLIC_API_BASE so it works in both
// server components (build-time inlined) and client components (runtime). Default
// is the local control plane at :8000.

export const API_BASE: string =
  process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";

export type HealthStatus = "ok" | "degraded" | "down";

export type ServiceHealth = {
  status: HealthStatus;
  // Field is `detail` (not `message`) — see ServiceHealth in app.py.
  detail: string;
};

export type HealthResponse = {
  status: HealthStatus;
  services: Record<string, ServiceHealth>;
};

export type Mode = "build" | "trading" | "pre_trade_freeze";

export type ModeTransition = {
  mode: string;
  at: string;
};

export type ModeResponse = {
  mode: Mode;
  as_of: string;
  next_transition: ModeTransition | null;
};

export type PMSummary = {
  id: string;
  name: string;
  status: string;
};

export type PMRecord = {
  id: string;
  name: string;
  status: string;
  starting_capital_inr: number;
  spawned_at: string;
  stopped_at: string | null;
  prompt_path: string;
  config: Record<string, unknown>;
  workflow_id: string | null;
};

export type JournalResponse = {
  pm_id: string;
  lines: string[];
};

export type PMStateChangeResponse = {
  pm_id: string;
  status: string;
};

class ApiError extends Error {
  constructor(
    message: string,
    public status?: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function getJSON<T>(path: string, signal?: AbortSignal): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { accept: "application/json" },
    signal,
    // The dashboard always wants live data, never an HTTP cache.
    cache: "no-store",
  });
  if (!res.ok) {
    throw new ApiError(`GET ${path} -> ${res.status}`, res.status);
  }
  return (await res.json()) as T;
}

async function postJSON<T>(path: string, signal?: AbortSignal): Promise<T> {
  // The state-change endpoints take no body (control plane reads pm_id
  // from the path). We send no Content-Type so FastAPI doesn't try to
  // parse one.
  const res = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: { accept: "application/json" },
    signal,
    cache: "no-store",
  });
  if (!res.ok) {
    throw new ApiError(`POST ${path} -> ${res.status}`, res.status);
  }
  return (await res.json()) as T;
}

export function fetchHealth(signal?: AbortSignal): Promise<HealthResponse> {
  return getJSON<HealthResponse>("/api/health", signal);
}

export function fetchMode(signal?: AbortSignal): Promise<ModeResponse> {
  return getJSON<ModeResponse>("/api/mode", signal);
}

export function fetchPMs(signal?: AbortSignal): Promise<PMSummary[]> {
  return getJSON<PMSummary[]>("/api/pms", signal);
}

export function fetchPM(id: string, signal?: AbortSignal): Promise<PMRecord> {
  return getJSON<PMRecord>(`/api/pms/${encodeURIComponent(id)}`, signal);
}

export function fetchJournal(
  id: string,
  lines = 50,
  signal?: AbortSignal,
): Promise<JournalResponse> {
  return getJSON<JournalResponse>(
    `/api/pms/${encodeURIComponent(id)}/journal?lines=${lines}`,
    signal,
  );
}

export function stopPM(id: string): Promise<PMStateChangeResponse> {
  return postJSON<PMStateChangeResponse>(`/api/pms/${encodeURIComponent(id)}/stop`);
}

export function pausePM(id: string): Promise<PMStateChangeResponse> {
  return postJSON<PMStateChangeResponse>(`/api/pms/${encodeURIComponent(id)}/pause`);
}

export function resumePM(id: string): Promise<PMStateChangeResponse> {
  return postJSON<PMStateChangeResponse>(`/api/pms/${encodeURIComponent(id)}/resume`);
}

export { ApiError };
