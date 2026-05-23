"use client";

// Event ticker for the home page (K2 Step 2.5). Subscribes to
// WS /api/stream and renders the last 20 events in a small bar at the
// bottom of the dashboard. On disconnect it clears state and reconnects
// with bounded exponential backoff (cap at 10s) — slow consumers drop
// events server-side, so a long disconnect is not silently masked.
//
// Configurable WS endpoint via NEXT_PUBLIC_WS_URL; defaults to derive
// from NEXT_PUBLIC_API_BASE so a single env in dev sets both.

import { useEffect, useRef, useState } from "react";

import { API_BASE } from "@/lib/api";

type StreamEvent = {
  type: string;
  ts: string;
  payload: Record<string, unknown>;
};

const MAX_EVENTS = 20;
const BACKOFF_INITIAL_MS = 500;
const BACKOFF_CAP_MS = 10_000;

function resolveWsUrl(): string {
  // Env override wins. Otherwise translate the API base — http→ws,
  // https→wss — and append the stream path.
  const explicit = process.env.NEXT_PUBLIC_WS_URL;
  if (explicit) return explicit;
  const base = API_BASE.replace(/^http/, "ws");
  return `${base}/api/stream`;
}

function formatPayload(ev: StreamEvent): string {
  // The dashboard cares about three event shapes; everything else
  // falls through to a JSON dump so we never silently swallow a new
  // event type added on the backend.
  switch (ev.type) {
    case "pm.heartbeat": {
      const pm = ev.payload.pm_id ?? "?";
      const mode = ev.payload.mode ?? "?";
      return `${pm} (${mode})`;
    }
    case "agent.lifecycle": {
      const id = ev.payload.agent_id ?? ev.payload.pm_id ?? "?";
      const e = ev.payload.event ?? "?";
      return `${id} ${e}`;
    }
    case "mode.changed": {
      return `${ev.payload.from ?? "?"} → ${ev.payload.to ?? "?"}`;
    }
    default:
      return JSON.stringify(ev.payload);
  }
}

function formatTime(ts: string): string {
  // Locale time-only renderer; the date comes for free in the WS event
  // but the ticker fits one event per row.
  try {
    return new Date(ts).toLocaleTimeString();
  } catch {
    return ts;
  }
}

export function EventTicker() {
  const [events, setEvents] = useState<StreamEvent[]>([]);
  const [connected, setConnected] = useState(false);
  // Refs for the live socket + reconnect timer so the cleanup tear-down
  // doesn't race a pending retry.
  const wsRef = useRef<WebSocket | null>(null);
  const retryRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    const url = resolveWsUrl();
    let cancelled = false;
    let attempt = 0;

    const connect = () => {
      if (cancelled) return;
      const ws = new WebSocket(url);
      wsRef.current = ws;

      ws.addEventListener("open", () => {
        attempt = 0;
        setConnected(true);
      });

      ws.addEventListener("message", (msg) => {
        try {
          const ev = JSON.parse(msg.data) as StreamEvent;
          setEvents((prev) => {
            const next = [ev, ...prev];
            return next.length > MAX_EVENTS ? next.slice(0, MAX_EVENTS) : next;
          });
        } catch {
          // Ignore malformed frames — the server only emits JSON, so
          // anything else is a bug we'd rather see in the network tab.
        }
      });

      const onClose = () => {
        if (cancelled) return;
        setConnected(false);
        // Clear so a fresh subscription doesn't show stale events
        // through what may be a long outage.
        setEvents([]);
        const delay = Math.min(
          BACKOFF_INITIAL_MS * 2 ** attempt,
          BACKOFF_CAP_MS,
        );
        attempt += 1;
        retryRef.current = setTimeout(connect, delay);
      };

      ws.addEventListener("close", onClose);
      // Treat error as close-equivalent — Chromium fires both, others
      // only fire one. The retry logic is idempotent.
      ws.addEventListener("error", () => {
        try {
          ws.close();
        } catch {
          /* ignore */
        }
      });
    };

    connect();

    return () => {
      cancelled = true;
      if (retryRef.current !== null) {
        clearTimeout(retryRef.current);
        retryRef.current = null;
      }
      if (wsRef.current !== null) {
        try {
          wsRef.current.close();
        } catch {
          /* ignore */
        }
        wsRef.current = null;
      }
    };
  }, []);

  return (
    <div className="rounded-md border bg-muted/30 p-3">
      <div className="mb-2 flex items-center justify-between text-xs uppercase tracking-wide text-muted-foreground">
        <span>Activity</span>
        <span>
          {connected ? (
            <span className="text-green-600">● live</span>
          ) : (
            <span className="text-yellow-600">○ reconnecting</span>
          )}
        </span>
      </div>
      {events.length === 0 ? (
        <div className="text-xs text-muted-foreground">
          Waiting for events...
        </div>
      ) : (
        <ul className="space-y-1 text-xs font-mono">
          {events.map((ev, i) => (
            <li
              key={`${ev.ts}-${i}`}
              className="flex gap-3 whitespace-nowrap"
            >
              <span className="text-muted-foreground">
                {formatTime(ev.ts)}
              </span>
              <span className="font-semibold">{ev.type}</span>
              <span className="truncate">{formatPayload(ev)}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
