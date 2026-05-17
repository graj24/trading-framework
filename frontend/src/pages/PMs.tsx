import { useState, useEffect, useRef, useCallback } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import ReactFlow, {
  Node, Edge, Background, Controls, MiniMap,
  useNodesState, useEdgesState, MarkerType,
} from "reactflow";
import "reactflow/dist/style.css";
import { api, PMSummary, PMEvent, PMState, AuditEntry, TriageDecision, Trade } from "@/lib/api";
import clsx from "clsx";

// ── Constants ─────────────────────────────────────────────────────────────────

const TOPIC_COLOR: Record<string, string> = {
  "price.spike": "#f59e0b",
  "news": "#3b82f6",
  "fill": "#10b981",
  "risk.breach": "#ef4444",
  "pm.wakeup": "#8b5cf6",
  "exec_order": "#f97316",
  "research": "#06b6d4",
  "system.daemon": "#6b7280",
};

function topicColor(topic: string) {
  for (const [k, v] of Object.entries(TOPIC_COLOR)) {
    if (topic.startsWith(k)) return v;
  }
  return "#6b7280";
}

// ── Flow canvas node/edge builders ────────────────────────────────────────────

function buildGraph(pm: PMSummary | null): { nodes: Node[]; edges: Edge[] } {
  if (!pm) return { nodes: [], edges: [] };
  const id = pm.pm_id;

  const nodes: Node[] = [
    {
      id: "tier1",
      data: { label: "Tier 1\nPublishers" },
      position: { x: 300, y: 20 },
      style: { background: "#1e293b", border: "1px solid #334155", color: "#94a3b8", fontSize: 11, whiteSpace: "pre", width: 120, textAlign: "center" },
    },
    {
      id: "events_db",
      data: { label: "events.db" },
      position: { x: 320, y: 130 },
      style: { background: "#1e293b", border: "1px solid #3b82f6", color: "#3b82f6", fontSize: 11, width: 90, textAlign: "center" },
    },
    {
      id: `pm_${id}`,
      data: { label: `PM${id}\n(Strategic)` },
      position: { x: 300, y: 240 },
      style: { background: "#1e293b", border: "2px solid #8b5cf6", color: "#c4b5fd", fontSize: 12, fontWeight: "bold", whiteSpace: "pre", width: 110, textAlign: "center" },
    },
    {
      id: `triage_${id}`,
      data: { label: `PM${id}.Triage` },
      position: { x: 100, y: 370 },
      style: { background: "#1e293b", border: "1px solid #f59e0b", color: "#fcd34d", fontSize: 11, width: 100, textAlign: "center" },
    },
    {
      id: `trader_${id}`,
      data: { label: `PM${id}.Trader` },
      position: { x: 300, y: 370 },
      style: { background: "#1e293b", border: "1px solid #10b981", color: "#6ee7b7", fontSize: 11, width: 100, textAlign: "center" },
    },
    {
      id: `risk_${id}`,
      data: { label: `PM${id}.Risk` },
      position: { x: 500, y: 370 },
      style: { background: "#1e293b", border: "1px solid #ef4444", color: "#fca5a5", fontSize: 11, width: 100, textAlign: "center" },
    },
    {
      id: "broker",
      data: { label: "Broker\n(Paper/Live)" },
      position: { x: 300, y: 490 },
      style: { background: "#1e293b", border: "1px solid #334155", color: "#94a3b8", fontSize: 11, whiteSpace: "pre", width: 100, textAlign: "center" },
    },
  ];

  const edges: Edge[] = [
    { id: "e1", source: "tier1", target: "events_db", animated: false, style: { stroke: "#334155" } },
    { id: "e2", source: "events_db", target: `triage_${id}`, animated: false, style: { stroke: "#334155" } },
    { id: "e3", source: `triage_${id}`, target: `pm_${id}`, label: "wakeup", style: { stroke: "#8b5cf6" }, markerEnd: { type: MarkerType.ArrowClosed } },
    { id: "e4", source: `triage_${id}`, target: `trader_${id}`, label: "exec", style: { stroke: "#f97316" }, markerEnd: { type: MarkerType.ArrowClosed } },
    { id: "e5", source: `pm_${id}`, target: `trader_${id}`, label: "order", style: { stroke: "#10b981" }, markerEnd: { type: MarkerType.ArrowClosed } },
    { id: "e6", source: `pm_${id}`, target: `risk_${id}`, label: "check", style: { stroke: "#ef4444" }, markerEnd: { type: MarkerType.ArrowClosed } },
    { id: "e7", source: `trader_${id}`, target: "broker", animated: false, style: { stroke: "#334155" } },
    { id: "e8", source: `risk_${id}`, target: `pm_${id}`, label: "breach", style: { stroke: "#ef4444", strokeDasharray: "4" }, markerEnd: { type: MarkerType.ArrowClosed } },
  ];

  return { nodes, edges };
}

// ── Sub-components ────────────────────────────────────────────────────────────

function PausePMButton({ pmId }: { pmId: string }) {
  const qc = useQueryClient();
  const { data } = useQuery({
    queryKey: ["pm_paused", pmId],
    queryFn: () => api.pmPausedStatus(pmId),
    refetchInterval: 5000,
  });
  const pause = useMutation({
    mutationFn: () => api.pmPause(pmId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["pm_paused", pmId] }),
  });
  const resume = useMutation({
    mutationFn: () => api.pmResume(pmId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["pm_paused", pmId] }),
  });

  const paused = data?.paused ?? false;
  return (
    <button
      onClick={() => paused ? resume.mutate() : pause.mutate()}
      className={clsx(
        "px-2 py-0.5 text-[10px] font-mono rounded border transition-colors",
        paused
          ? "bg-yellow-900/40 border-yellow-600 text-yellow-300 hover:bg-yellow-800/40"
          : "bg-bg-tertiary border-border text-text-muted hover:border-yellow-600 hover:text-yellow-400"
      )}
    >
      {paused ? "▶ Resume PM" : "⏸ Pause PM"}
    </button>
  );
}

function KillSwitchButton() {
  const qc = useQueryClient();
  const { data } = useQuery({ queryKey: ["kill_switch"], queryFn: api.killSwitchStatus, refetchInterval: 5000 });
  const on = useMutation({ mutationFn: () => api.killSwitchOn(), onSuccess: () => qc.invalidateQueries({ queryKey: ["kill_switch"] }) });
  const off = useMutation({ mutationFn: () => api.killSwitchOff(), onSuccess: () => qc.invalidateQueries({ queryKey: ["kill_switch"] }) });

  const active = data?.active ?? false;
  return (
    <button
      onClick={() => active ? off.mutate() : on.mutate()}
      className={clsx(
        "px-3 py-1 text-xs font-mono rounded border transition-colors",
        active
          ? "bg-red-900/60 border-red-500 text-red-300 hover:bg-red-800/60"
          : "bg-bg-secondary border-border text-text-muted hover:border-red-500 hover:text-red-400"
      )}
    >
      {active ? "🔴 KILL SWITCH ACTIVE — click to deactivate" : "⏸ Kill Switch"}
    </button>
  );
}

function PMCard({ pm, selected, onClick }: { pm: PMSummary; selected: boolean; onClick: () => void }) {
  const pnl = pm.daily_pnl_inr;
  const { data: pauseData } = useQuery({
    queryKey: ["pm_paused", pm.pm_id],
    queryFn: () => api.pmPausedStatus(pm.pm_id),
    refetchInterval: 5000,
  });
  const paused = pauseData?.paused ?? false;

  return (
    <button
      onClick={onClick}
      className={clsx(
        "w-full text-left p-3 rounded border transition-colors",
        selected
          ? "bg-blue/10 border-blue/50"
          : "bg-bg-secondary border-border hover:border-border/80"
      )}
    >
      <div className="flex items-center justify-between mb-1">
        <span className="text-xs font-bold text-text-primary">PM{pm.pm_id}</span>
        <span className={clsx(
          "w-2 h-2 rounded-full",
          paused ? "bg-yellow-400" : pm.active ? "bg-green-400" : "bg-gray-500"
        )} />
      </div>
      <div className={clsx("text-xs font-mono", pnl >= 0 ? "text-green-400" : "text-red-400")}>
        {pnl >= 0 ? "+" : ""}₹{pnl.toFixed(0)}
      </div>
      <div className="text-[10px] text-text-muted mt-0.5">
        {paused ? "⏸ paused" : `${pm.open_positions} open · ${pm.inbox_count} inbox`}
      </div>
    </button>
  );
}

function EventTicker({ events }: { events: PMEvent[] }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (ref.current) ref.current.scrollLeft = ref.current.scrollWidth;
  }, [events]);

  return (
    <div ref={ref} className="flex gap-2 overflow-x-auto scrollbar-none px-2 py-1 bg-bg-secondary border-t border-border">
      {events.slice(-30).map((e) => (
        <span
          key={e.id}
          className="shrink-0 text-[10px] font-mono px-2 py-0.5 rounded"
          style={{ background: topicColor(e.topic) + "22", color: topicColor(e.topic), border: `1px solid ${topicColor(e.topic)}44` }}
        >
          {e.topic.split(".").slice(0, 2).join(".")}
          {e.payload?.symbol ? ` · ${e.payload.symbol}` : ""}
        </span>
      ))}
    </div>
  );
}

function TraceTimeline({ events, pmId }: { events: PMEvent[]; pmId: string }) {
  const [selected, setSelected] = useState<PMEvent | null>(null);
  const relevant = events.filter(
    (e) => e.pm_id === pmId || e.topic.includes(`.${pmId}`)
  ).slice(-50);

  return (
    <div className="flex flex-col h-full">
      <div className="text-[10px] text-text-muted tracking-widest px-3 pt-2 pb-1 uppercase">
        Flow Trace — PM{pmId}
      </div>
      <div className="flex-1 overflow-y-auto">
        {relevant.length === 0 && (
          <div className="text-xs text-text-muted px-3 py-4">No events yet for PM{pmId}</div>
        )}
        {relevant.map((e) => (
          <button
            key={e.id}
            onClick={() => setSelected(selected?.id === e.id ? null : e)}
            className="w-full text-left px-3 py-1.5 border-b border-border/30 hover:bg-bg-tertiary/40 transition-colors"
          >
            <div className="flex items-center gap-2">
              <span
                className="w-2 h-2 rounded-full shrink-0"
                style={{ background: topicColor(e.topic) }}
              />
              <span className="text-[10px] font-mono text-text-muted w-16 shrink-0">
                {e.ts.slice(11, 19)}
              </span>
              <span className="text-xs font-mono text-text-primary truncate">{e.topic}</span>
              <span
                className="text-[10px] px-1 rounded shrink-0"
                style={{ background: topicColor(e.topic) + "22", color: topicColor(e.topic) }}
              >
                {e.severity}
              </span>
            </div>
            {selected?.id === e.id && (
              <pre className="mt-1 text-[10px] text-text-muted bg-bg-tertiary rounded p-2 overflow-x-auto">
                {JSON.stringify(e.payload, null, 2)}
              </pre>
            )}
          </button>
        ))}
      </div>
    </div>
  );
}

type DetailTab = "plan" | "journal" | "tasks" | "inbox" | "trades" | "audit" | "triage" | "trace";

function DetailPanel({ pmId, events }: { pmId: string; events: PMEvent[] }) {
  const [tab, setTab] = useState<DetailTab>("plan");

  const { data: state } = useQuery<PMState>({
    queryKey: ["pm_state", pmId],
    queryFn: () => api.pmState(pmId),
    refetchInterval: 15000,
  });
  const { data: audit } = useQuery<AuditEntry[]>({
    queryKey: ["pm_audit", pmId],
    queryFn: () => api.pmAudit(pmId),
    enabled: tab === "audit",
    refetchInterval: 10000,
  });
  const { data: triage } = useQuery<TriageDecision[]>({
    queryKey: ["pm_triage", pmId],
    queryFn: () => api.pmTriageLog(pmId),
    enabled: tab === "triage",
    refetchInterval: 5000,
  });
  const { data: trades } = useQuery<Trade[]>({
    queryKey: ["pm_trades", pmId],
    queryFn: () => api.pmTrades(pmId),
    enabled: tab === "trades",
    refetchInterval: 10000,
  });

  const TABS: { id: DetailTab; label: string }[] = [
    { id: "plan", label: "Plan" },
    { id: "journal", label: "Journal" },
    { id: "tasks", label: "Tasks" },
    { id: "inbox", label: "Inbox" },
    { id: "trades", label: "Trades" },
    { id: "audit", label: "Audit" },
    { id: "triage", label: "Triage" },
    { id: "trace", label: "Trace" },
  ];

  return (
    <div className="flex flex-col h-full">
      {/* Tab bar */}
      <div className="flex gap-0.5 px-2 pt-2 pb-0 border-b border-border overflow-x-auto scrollbar-none">
        {TABS.map((t) => (
          <button
            key={t.id}
            onClick={() => setTab(t.id)}
            className={clsx(
              "px-2 py-1 text-[10px] tracking-wide rounded-t transition-colors shrink-0",
              tab === t.id
                ? "bg-bg-tertiary text-text-primary border border-b-0 border-border"
                : "text-text-muted hover:text-text-primary"
            )}
          >
            {t.label.toUpperCase()}
          </button>
        ))}
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto p-3 text-xs font-mono">
        {tab === "plan" && (
          <pre className="whitespace-pre-wrap text-text-primary leading-relaxed">
            {state?.plan || "(no plan yet)"}
          </pre>
        )}

        {tab === "journal" && (
          <pre className="whitespace-pre-wrap text-text-primary leading-relaxed">
            {state?.journal || "(no journal entries yet)"}
          </pre>
        )}

        {tab === "tasks" && (
          <div className="space-y-3">
            {["backlog", "in_progress", "done"].map((col) => (
              <div key={col}>
                <div className="text-[10px] text-text-muted uppercase tracking-widest mb-1">{col.replace("_", " ")}</div>
                {((state?.tasks as Record<string, unknown[]>)?.[col] ?? []).length === 0
                  ? <div className="text-text-muted text-[10px]">(empty)</div>
                  : ((state?.tasks as Record<string, unknown[]>)?.[col] ?? []).map((t, i) => (
                    <div key={i} className="bg-bg-tertiary rounded px-2 py-1 mb-1 text-text-primary text-[11px]">
                      {typeof t === "string" ? t : JSON.stringify(t)}
                    </div>
                  ))
                }
              </div>
            ))}
          </div>
        )}

        {tab === "inbox" && (
          <div className="space-y-1">
            {(state?.inbox ?? []).length === 0
              ? <div className="text-text-muted">(inbox empty)</div>
              : (state?.inbox ?? []).map((e, i) => (
                <div key={i} className="bg-bg-tertiary rounded px-2 py-1 text-[10px] text-text-primary">
                  <pre className="whitespace-pre-wrap">{JSON.stringify(e, null, 2)}</pre>
                </div>
              ))
            }
          </div>
        )}

        {tab === "trades" && (
          <table className="w-full text-[10px]">
            <thead>
              <tr className="text-text-muted border-b border-border">
                <th className="text-left py-1">Symbol</th>
                <th className="text-right py-1">Entry</th>
                <th className="text-right py-1">P&L</th>
                <th className="text-left py-1">Outcome</th>
              </tr>
            </thead>
            <tbody>
              {(trades ?? []).map((t, i) => (
                <tr key={i} className="border-b border-border/30">
                  <td className="py-1 text-text-primary">{t.symbol}</td>
                  <td className="py-1 text-right text-text-muted">₹{t.entry_price?.toFixed(2)}</td>
                  <td className={clsx("py-1 text-right", (t.pnl_inr ?? 0) >= 0 ? "text-green-400" : "text-red-400")}>
                    {t.pnl_inr != null ? `₹${t.pnl_inr.toFixed(0)}` : "open"}
                  </td>
                  <td className="py-1 text-text-muted">{t.outcome ?? "open"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}

        {tab === "audit" && (
          <div className="space-y-1">
            {(audit ?? []).slice().reverse().map((e, i) => (
              <div key={i} className={clsx(
                "rounded px-2 py-1 text-[10px]",
                e.event === "ORDER_BLOCKED" || e.event === "DAILY_HALT" ? "bg-red-900/20 text-red-300" :
                e.event === "ORDER_PLACED" ? "bg-green-900/20 text-green-300" :
                "bg-bg-tertiary text-text-muted"
              )}>
                <span className="text-text-muted mr-2">{e.ts.slice(11, 19)}</span>
                <span className="font-bold">{e.event}</span>
                {/* eslint-disable-next-line @typescript-eslint/no-explicit-any */}
                {(e as any).symbol && <span className="ml-2">{String((e as any).symbol)}</span>}
                {/* eslint-disable-next-line @typescript-eslint/no-explicit-any */}
                {(e as any).reason && <span className="ml-2 text-text-muted">{String((e as any).reason)}</span>}
              </div>
            ))}
          </div>
        )}

        {tab === "triage" && (
          <div className="space-y-1">
            {(triage ?? []).slice().reverse().map((d, i) => (
              <div key={i} className="flex items-center gap-2 border-b border-border/30 py-1">
                <span className="text-text-muted w-16 shrink-0">{d.ts.slice(11, 19)}</span>
                <span
                  className="px-1 rounded text-[10px] shrink-0"
                  style={{ background: topicColor(d.topic) + "22", color: topicColor(d.topic) }}
                >
                  {d.classification}
                </span>
                <span className="text-text-primary truncate">{d.topic}</span>
                {d.symbol && <span className="text-text-muted shrink-0">{d.symbol}</span>}
              </div>
            ))}
          </div>
        )}

        {tab === "trace" && (
          <TraceTimeline events={events} pmId={pmId} />
        )}
      </div>
    </div>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

export function PMs() {
  const [selectedPmId, setSelectedPmId] = useState<string | null>(null);
  const [liveEvents, setLiveEvents] = useState<PMEvent[]>([]);
  const [replayMode, setReplayMode] = useState(false);
  const [replayFromId, setReplayFromId] = useState(0);
  const wsRef = useRef<WebSocket | null>(null);
  const cursorRef = useRef(0);

  const { data: pms, refetch: refetchPms } = useQuery<PMSummary[]>({
    queryKey: ["pms"],
    queryFn: api.pms,
    refetchInterval: 10000,
  });

  const selectedPm = pms?.find((p) => p.pm_id === selectedPmId) ?? null;

  // Build react-flow graph
  const { nodes: initNodes, edges: initEdges } = buildGraph(selectedPm);
  const [nodes, , onNodesChange] = useNodesState(initNodes);
  const [edges, , onEdgesChange] = useEdgesState(initEdges);

  // Animate edges when relevant events arrive
  const [activeEdges, setActiveEdges] = useState<Set<string>>(new Set());

  const animateEdge = useCallback((edgeId: string) => {
    setActiveEdges((prev) => new Set([...prev, edgeId]));
    setTimeout(() => setActiveEdges((prev) => { const n = new Set(prev); n.delete(edgeId); return n; }), 1500);
  }, []);

  const handleEvent = useCallback((event: PMEvent) => {
    setLiveEvents((prev) => [...prev.slice(-500), event]);
    if (!selectedPmId) return;
    const id = selectedPmId;
    const t = event.topic;
    if (t.startsWith("price.spike") || t.startsWith("news")) animateEdge("e1");
    if (t === `pm.wakeup.${id}`) animateEdge("e3");
    if (t === `exec_order.${id}`) { animateEdge("e4"); animateEdge("e5"); }
    if (t === `fill.${id}`) animateEdge("e7");
    if (t === `risk.breach.${id}`) { animateEdge("e6"); animateEdge("e8"); }
  }, [selectedPmId, animateEdge]);

  // WebSocket connection
  useEffect(() => {
    const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    const url = `${proto}//${window.location.host}/ws/pm_events`;
    const ws = new WebSocket(url);
    wsRef.current = ws;

    ws.onmessage = (e) => {
      try {
        const msg = JSON.parse(e.data);
        if (msg.type === "cursor") cursorRef.current = msg.latest_id;
        if (msg.type === "pm_event") handleEvent(msg.event as PMEvent);
      } catch {}
    };
    ws.onclose = () => setTimeout(() => {}, 3000);

    return () => ws.close();
  }, [handleEvent]);

  // Replay: seek WS to a past event id
  const startReplay = (fromId: number) => {
    setReplayMode(true);
    setReplayFromId(fromId);
    setLiveEvents([]);
    wsRef.current?.send(JSON.stringify({ type: "seek", from_id: fromId }));
  };

  const stopReplay = () => {
    setReplayMode(false);
    wsRef.current?.send(JSON.stringify({ type: "seek", from_id: cursorRef.current }));
    setLiveEvents([]);
  };

  // Animated edges overlay
  const animatedEdges = edges.map((e) => ({
    ...e,
    animated: activeEdges.has(e.id),
    style: {
      ...e.style,
      strokeWidth: activeEdges.has(e.id) ? 2 : 1,
    },
  }));

  return (
    <div className="flex flex-col h-full overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-2 border-b border-border bg-bg-secondary shrink-0">
        <div className="flex items-center gap-3">
          <span className="text-xs font-bold tracking-widest text-text-primary">PMs</span>
          {replayMode && (
            <span className="text-[10px] bg-yellow-900/40 text-yellow-300 border border-yellow-700 px-2 py-0.5 rounded">
              ↺ REPLAY from id={replayFromId}
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          {replayMode ? (
            <button onClick={stopReplay} className="text-[10px] px-2 py-1 rounded border border-yellow-600 text-yellow-400 hover:bg-yellow-900/30">
              Stop Replay
            </button>
          ) : (
            <button
              onClick={() => {
                const id = prompt("Replay from event id:");
                if (id) startReplay(parseInt(id));
              }}
              className="text-[10px] px-2 py-1 rounded border border-border text-text-muted hover:text-text-primary"
            >
              ↺ Replay
            </button>
          )}
          <KillSwitchButton />
        </div>
      </div>

      {/* Body */}
      <div className="flex flex-1 overflow-hidden">
        {/* Left rail */}
        <div className="w-44 shrink-0 border-r border-border bg-bg-secondary flex flex-col overflow-y-auto">
          <div className="text-[10px] text-text-muted tracking-widest px-3 pt-3 pb-1 uppercase">Portfolio Managers</div>
          <div className="flex flex-col gap-1 px-2 pb-2">
            {(pms ?? []).map((pm) => (
              <PMCard
                key={pm.pm_id}
                pm={pm}
                selected={selectedPmId === pm.pm_id}
                onClick={() => setSelectedPmId(pm.pm_id)}
              />
            ))}
            {(pms ?? []).length === 0 && (
              <div className="text-[10px] text-text-muted px-1 py-2">No PMs registered yet.<br />Run scripts/register_pm.py</div>
            )}
          </div>
        </div>

        {/* Center canvas */}
        <div className="flex-1 flex flex-col overflow-hidden">
          <div className="flex-1 relative">
            {selectedPmId ? (
              <ReactFlow
                nodes={nodes}
                edges={animatedEdges}
                onNodesChange={onNodesChange}
                onEdgesChange={onEdgesChange}
                fitView
                proOptions={{ hideAttribution: true }}
              >
                <Background color="#1e293b" gap={20} />
                <Controls />
                <MiniMap nodeColor="#334155" maskColor="#0f172a88" />
              </ReactFlow>
            ) : (
              <div className="flex items-center justify-center h-full text-text-muted text-sm">
                Select a PM from the left to see its flow
              </div>
            )}
          </div>
          {/* Event ticker */}
          <EventTicker events={liveEvents} />
        </div>

        {/* Right detail panel */}
        {selectedPmId && (
          <div className="w-80 shrink-0 border-l border-border bg-bg-secondary flex flex-col overflow-hidden">
            <div className="px-3 pt-2 pb-1 border-b border-border shrink-0">
              <div className="flex items-center justify-between">
                <div className="text-xs font-bold text-text-primary">PM{selectedPmId}</div>
                <PausePMButton pmId={selectedPmId} />
              </div>
              {selectedPm && (
                <div className="flex gap-3 mt-1">
                  <span className={clsx("text-xs font-mono", selectedPm.daily_pnl_inr >= 0 ? "text-green-400" : "text-red-400")}>
                    {selectedPm.daily_pnl_inr >= 0 ? "+" : ""}₹{selectedPm.daily_pnl_inr.toFixed(0)} today
                  </span>
                  <span className="text-[10px] text-text-muted">{selectedPm.open_positions} open</span>
                </div>
              )}
            </div>
            <div className="flex-1 overflow-hidden">
              <DetailPanel pmId={selectedPmId} events={liveEvents} />
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
