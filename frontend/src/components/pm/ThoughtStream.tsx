/**
 * ThoughtStream — live event feed for a single PM.
 * Connects to /ws/pm_events?pm_id=X and renders each event as a styled row.
 */
import { useEffect, useRef, useState, useCallback } from "react";
import { cn } from "@/lib/cn";
import { fmt } from "@/lib/formatters";

export interface StreamEvent {
  id: number;
  topic: string;
  payload: Record<string, unknown>;
  pm_id: string | null;
  severity: string;
  ts: string;
}

// ── Topic → visual config ─────────────────────────────────────────────────────

const TOPIC_META: { prefix: string; label: string; color: string; bg: string }[] = [
  { prefix: "agent.thinking",  label: "thinking",  color: "text-accent-purple",  bg: "bg-accent-purple/10 border-accent-purple/30" },
  { prefix: "fill",            label: "fill",      color: "text-accent-success", bg: "bg-accent-success/10 border-accent-success/30" },
  { prefix: "exec_order",      label: "order",     color: "text-accent-warning", bg: "bg-accent-warning/10 border-accent-warning/30" },
  { prefix: "risk.breach",     label: "risk",      color: "text-accent-danger",  bg: "bg-accent-danger/10 border-accent-danger/30" },
  { prefix: "pm.wakeup",       label: "wakeup",    color: "text-accent-info",    bg: "bg-accent-info/10 border-accent-info/30" },
  { prefix: "price.spike",     label: "price",     color: "text-accent-warning", bg: "bg-accent-warning/5 border-surface-700" },
  { prefix: "news",            label: "news",      color: "text-accent-info",    bg: "bg-accent-info/5 border-surface-700" },
  { prefix: "research",        label: "research",  color: "text-accent-info",    bg: "bg-accent-info/5 border-surface-700" },
  { prefix: "system.daemon",   label: "daemon",    color: "text-content-muted",  bg: "bg-surface-800 border-surface-700" },
  { prefix: "system",          label: "system",    color: "text-content-muted",  bg: "bg-surface-800 border-surface-700" },
];

function topicMeta(topic: string) {
  return TOPIC_META.find((m) => topic.startsWith(m.prefix)) ?? TOPIC_META[TOPIC_META.length - 1];
}

function eventSummary(topic: string, payload: Record<string, unknown>): string {
  if (topic.startsWith("agent.thinking")) {
    const agent = payload.agent as string ?? "";
    if (payload.status === "start") return `[${agent}] thinking… ${payload.context ?? ""}`;
    if (payload.status === "done")  return `[${agent}] → ${payload.output ?? payload.decision ?? "done"}`;
  }
  if (topic.startsWith("fill"))       return `Filled ${payload.qty}×${payload.symbol} @ ₹${payload.price}`;
  if (topic.startsWith("exec_order")) return `Order: ${payload.side ?? "BUY"} ${payload.qty}×${payload.symbol}`;
  if (topic.startsWith("risk.breach"))return `Risk breach: ${payload.reason ?? topic}`;
  if (topic.startsWith("pm.wakeup"))  return `Wakeup triggered by ${payload.trigger ?? "scheduler"}`;
  if (topic.startsWith("price.spike"))return `${payload.symbol} ${(payload.pct as number) > 0 ? "▲" : "▼"}${Math.abs(payload.pct as number ?? 0).toFixed(2)}%`;
  if (topic.startsWith("news"))       return `News: ${payload.headline ?? payload.symbol ?? topic}`;
  if (topic.startsWith("research"))   return `Research queued: ${(payload.payload as any)?.symbol ?? ""}`;
  if (topic.startsWith("system.daemon")) return `Daemon ${payload.daemon} ${payload.event}`;
  return JSON.stringify(payload).slice(0, 80);
}

// ── Individual event row ──────────────────────────────────────────────────────

function EventRow({ ev, expanded, onToggle }: {
  ev: StreamEvent;
  expanded: boolean;
  onToggle: () => void;
}) {
  const meta = topicMeta(ev.topic);
  const isThinking = ev.topic.startsWith("agent.thinking") && (ev.payload.status as string) === "start";
  const summary = eventSummary(ev.topic, ev.payload);
  const time = ev.ts ? new Date(ev.ts + "Z").toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", second: "2-digit" }) : "";

  return (
    <div
      className={cn(
        "border rounded-lg px-3 py-2 cursor-pointer transition-all animate-fade-in",
        meta.bg,
        expanded ? "ring-1 ring-white/10" : "hover:brightness-110"
      )}
      onClick={onToggle}
    >
      <div className="flex items-start gap-2">
        <span className="text-[10px] text-content-muted font-mono mt-0.5 shrink-0 w-16">{time}</span>
        <span className={cn("text-[10px] font-bold uppercase tracking-wider shrink-0 w-14", meta.color)}>
          {meta.label}
        </span>
        <span className={cn("text-xs flex-1 leading-relaxed", isThinking ? "text-content-muted italic" : "text-content-primary")}>
          {isThinking ? (
            <span className="flex items-center gap-1.5">
              {summary}
              <span className="inline-flex gap-0.5">
                <span className="w-1 h-1 rounded-full bg-accent-purple animate-bounce" style={{ animationDelay: "0ms" }} />
                <span className="w-1 h-1 rounded-full bg-accent-purple animate-bounce" style={{ animationDelay: "150ms" }} />
                <span className="w-1 h-1 rounded-full bg-accent-purple animate-bounce" style={{ animationDelay: "300ms" }} />
              </span>
            </span>
          ) : summary}
        </span>
      </div>
      {expanded && (
        <pre className="mt-2 text-[10px] text-content-muted font-mono bg-surface-950 rounded p-2 overflow-auto max-h-32 whitespace-pre-wrap">
          {JSON.stringify(ev.payload, null, 2)}
        </pre>
      )}
    </div>
  );
}

// ── Main component ────────────────────────────────────────────────────────────

interface Props {
  pmId: string;
  /** Called whenever a new event arrives — lets parent react (e.g. pulse agent node) */
  onEvent?: (ev: StreamEvent) => void;
}

export function ThoughtStream({ pmId, onEvent }: Props) {
  const [events, setEvents] = useState<StreamEvent[]>([]);
  const [expanded, setExpanded] = useState<number | null>(null);
  const [paused, setPaused] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const pausedRef = useRef(false);
  pausedRef.current = paused;

  const connect = useCallback(() => {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${location.host}/ws/pm_events?pm_id=${pmId}`);
    wsRef.current = ws;

    ws.onmessage = (e) => {
      const msg = JSON.parse(e.data);
      if (msg.type === "pm_event") {
        const ev: StreamEvent = msg.event;
        if (!pausedRef.current) {
          setEvents((prev) => {
            const next = [...prev, ev];
            return next.length > 200 ? next.slice(-200) : next;
          });
          onEvent?.(ev);
        }
      }
    };

    ws.onclose = () => setTimeout(connect, 2000);
    ws.onerror = () => ws.close();

    // Heartbeat
    const hb = setInterval(() => ws.readyState === WebSocket.OPEN && ws.send(JSON.stringify({ type: "ping" })), 15000);
    return () => { clearInterval(hb); ws.close(); };
  }, [pmId, onEvent]);

  useEffect(() => {
    const cleanup = connect();
    return cleanup;
  }, [connect]);

  // Auto-scroll to bottom unless paused
  useEffect(() => {
    if (!paused) bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [events, paused]);

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="panel-header border-b border-surface-700 shrink-0">
        <span className="panel-title flex items-center gap-1.5">
          <span className="w-1.5 h-1.5 rounded-full bg-accent-success animate-pulse" />
          Thought Stream
        </span>
        <div className="flex items-center gap-2">
          <span className="text-[10px] text-content-muted">{events.length} events</span>
          <button
            onClick={() => setPaused((p) => !p)}
            className={cn("text-[10px] px-2 py-0.5 rounded border transition-colors",
              paused ? "border-accent-warning text-accent-warning" : "border-surface-600 text-content-muted hover:border-surface-500"
            )}
          >
            {paused ? "▶ Resume" : "⏸ Pause"}
          </button>
          <button
            onClick={() => setEvents([])}
            className="text-[10px] px-2 py-0.5 rounded border border-surface-600 text-content-muted hover:border-surface-500"
          >
            Clear
          </button>
        </div>
      </div>

      {/* Feed */}
      <div className="flex-1 overflow-y-auto p-2 flex flex-col gap-1.5">
        {events.length === 0 && (
          <div className="flex flex-col items-center justify-center h-full gap-2 text-content-muted">
            <span className="text-2xl">💭</span>
            <span className="text-xs">Waiting for events…</span>
          </div>
        )}
        {events.map((ev) => (
          <EventRow
            key={ev.id}
            ev={ev}
            expanded={expanded === ev.id}
            onToggle={() => setExpanded((x) => x === ev.id ? null : ev.id)}
          />
        ))}
        <div ref={bottomRef} />
      </div>
    </div>
  );
}
