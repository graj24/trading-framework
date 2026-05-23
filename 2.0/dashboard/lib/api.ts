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

export function fetchHealth(signal?: AbortSignal): Promise<HealthResponse> {
  return getJSON<HealthResponse>("/api/health", signal);
}

export function fetchMode(signal?: AbortSignal): Promise<ModeResponse> {
  return getJSON<ModeResponse>("/api/mode", signal);
}

export function fetchPMs(signal?: AbortSignal): Promise<PMSummary[]> {
  return getJSON<PMSummary[]>("/api/pms", signal);
}

export { ApiError };
