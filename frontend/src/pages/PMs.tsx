import { useState, useEffect, useRef, useCallback } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import ReactFlow, {
  Node, Edge, Background, Controls,
  useNodesState, useEdgesState, MarkerType,
} from "reactflow";
import "reactflow/dist/style.css";
import { api, PMSummary, PMEvent, PMState, AuditEntry, TriageDecision, Trade } from "@/lib/api";
import { Badge } from "@/components/ui/Badge";
import { Card, CardHeader } from "@/components/ui/Card";
import { cn } from "@/lib/cn";
import { fmt } from "@/lib/formatters";
import {
  TrendingUp, TrendingDown, ShieldOff, Shield, Pause, Play,
  AlertTriangle, Activity, Briefcase, Zap,
} from "lucide-react";
import {
  LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, Legend,
} from "recharts";

// ── Helpers ───────────────────────────────────────────────────────────────────

const TOPIC_COLOR: Record<string, string> = {
  "price.spike": "#f59e0b",
  "news":        "#3b82f6",
  "fill":        "#10b981",
  "risk.breach": "#ef4444",
  "pm.wakeup":   "#8b5cf6",
  "exec_order":  "#f97316",
  "research":    "#06b6d4",
  "system":      "#6b7280",
};
function topicColor(topic: string) {
  for (const [k, v] of Object.entries(TOPIC_COLOR)) if (topic.startsWith(k)) return v;
  return "#6b7280";
}

const PM_COLORS = ["#3b82f6", "#10b981", "#f59e0b", "#8b5cf6", "#ef4444", "#06b6d4"];

// ── Kill Switch ───────────────────────────────────────────────────────────────

function KillSwitch() {
  const qc = useQueryClient();
  const { data } = useQuery({ queryKey: ["kill_switch"], queryFn: api.killSwitchStatus, refetchInterval: 5000 });
  const on  = useMutation({ mutationFn: () => api.killSwitchOn(),  onSuccess: () => qc.invalidateQueries({ queryKey: ["kill_switch"] }) });
  const off = useMutation({ mutationFn: () => api.killSwitchOff(), onSuccess: () => qc.invalidateQueries({ queryKey: ["kill_switch"] }) });
  const active = data?.active ?? false;

  return (
    <button
      onClick={() => active ? off.mutate() : on.mutate()}
      className={cn(
        "flex items-center gap-2 px-3 py-1.5 rounded-lg border text-xs font-semibold transition-all",
        active
          ? "bg-accent-danger/20 border-accent-danger text-accent-danger animate-pulse-glow"
          : "bg-surface-700 border-surface-600 text-content-secondary hover:border-accent-danger hover:text-accent-danger"
      )}
    >
      {active ? <ShieldOff size={13} /> : <Shield size={13} />}
      {active ? "KILL SWITCH ACTIVE" : "Kill Switch"}
    </button>
  );
}

// ── PM Leaderboard card ───────────────────────────────────────────────────────

function PMCard({ pm, rank, selected, color, onClick }: {
  pm: PMSummary; rank: number; selected: boolean; color: string; onClick: () => void;
}) {
  const qc = useQueryClient();
  const { data: pauseData } = useQuery({
    queryKey: ["pm_paused", pm.pm_id],
    queryFn: () => api.pmPausedStatus(pm.pm_id),
    refetchInterval: 5000,
  });
  const pause  = useMutation({ mutationFn: () => api.pmPause(pm.pm_id),  onSuccess: () => qc.invalidateQueries({ queryKey: ["pm_paused", pm.pm_id] }) });
  const resume = useMutation({ mutationFn: () => api.pmResume(pm.pm_id), onSuccess: () => qc.invalidateQueries({ queryKey: ["pm_paused", pm.pm_id] }) });
  const paused = pauseData?.paused ?? false;
  const up = pm.daily_pnl_inr >= 0;

  return (
    <div
      onClick={onClick}
      className={cn(
        "relative flex items-center gap-3 p-3 rounded-xl border cursor-pointer transition-all",
        selected
          ? "border-opacity-60 bg-surface-750"
          : "border-surface-700 bg-surface-800 hover:border-surface-600"
      )}
      style={selected ? { borderColor: color } : {}}
    >
      {/* Rank */}
      <div className="w-6 h-6 rounded-full flex items-center justify-center text-xs font-bold shrink-0"
        style={{ background: color + "20", color }}>
        {rank}
      </div>

      {/* Info */}
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span className="text-sm font-bold text-content-primary">PM{pm.pm_id}</span>
          <span className={cn("w-1.5 h-1.5 rounded-full", paused ? "bg-accent-warning" : pm.active ? "bg-accent-success animate-pulse" : "bg-content-muted")} />
          {paused && <Badge variant="warning" className="text-[9px] py-0">PAUSED</Badge>}
        </div>
        <div className={cn("num text-sm font-semibold", up ? "positive" : "negative")}>
          {up ? "+" : ""}{fmt.inr(pm.daily_pnl_inr)}
        </div>
        <div className="text-[10px] text-content-muted">{pm.open_positions} open · {pm.inbox_count} inbox</div>
      </div>

      {/* Pause/resume */}
      <button
        onClick={(e) => { e.stopPropagation(); paused ? resume.mutate() : pause.mutate(); }}
        className={cn("p-1.5 rounded-lg transition-colors", paused ? "text-accent-warning hover:bg-accent-warning/10" : "text-content-muted hover:text-content-primary hover:bg-surface-700")}
      >
        {paused ? <Play size={12} /> : <Pause size={12} />}
      </button>
    </div>
  );
}

// ── Equity race chart ─────────────────────────────────────────────────────────

function EquityRace({ pms, trades }: { pms: PMSummary[]; trades: Record<string, Trade[]> }) {
  const data = useRef<Record<string, number>[]>([]);

  // Build cumulative P&L series per PM
  const series = pms.map((pm, i) => {
    const pmTrades = (trades[pm.pm_id] ?? [])
      .filter((t) => t.exit_date && t.pnl_inr != null)
      .sort((a, b) => new Date(a.exit_date!).getTime() - new Date(b.exit_date!).getTime());
    let cum = 0;
    return pmTrades.map((t) => ({ date: t.exit_date!.slice(5, 10), [`PM${pm.pm_id}`]: (cum += t.pnl_inr!) }));
  });

  // Merge all dates
  const allDates = [...new Set(series.flat().map((d) => d.date))].sort();
  const merged = allDates.map((date) => {
    const row: Record<string, unknown> = { date };
    series.forEach((s, i) => {
      const filtered = s.filter((d) => d.date <= date);
      const point = filtered[filtered.length - 1];
      row[`PM${pms[i].pm_id}`] = point ? Object.values(point)[1] : 0;
    });
    return row;
  });

  if (merged.length === 0) {
    return (
      <div className="flex items-center justify-center h-full text-xs text-content-muted">
        No closed trades yet
      </div>
    );
  }

  return (
    <ResponsiveContainer width="100%" height="100%">
      <LineChart data={merged} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
        <XAxis dataKey="date" tick={{ fill: "#6b7280", fontSize: 9 }} tickLine={false} axisLine={false} />
        <YAxis tick={{ fill: "#6b7280", fontSize: 9 }} tickLine={false} axisLine={false} tickFormatter={(v) => "₹" + v} width={40} />
        <Tooltip
          contentStyle={{ background: "#111827", border: "1px solid #1f2937", borderRadius: 8, fontSize: 11 }}
          formatter={(v: number, name: string) => [fmt.inr(v), name]}
        />
        <Legend wrapperStyle={{ fontSize: 10, color: "#6b7280" }} />
        {pms.map((pm, i) => (
          <Line
            key={pm.pm_id}
            type="monotone"
            dataKey={`PM${pm.pm_id}`}
            stroke={PM_COLORS[i % PM_COLORS.length]}
            strokeWidth={2}
            dot={false}
          />
        ))}
      </LineChart>
    </ResponsiveContainer>
  );
}

// ── Event ticker ──────────────────────────────────────────────────────────────

function EventTicker({ events }: { events: PMEvent[] }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => { if (ref.current) ref.current.scrollLeft = ref.current.scrollWidth; }, [events]);

  return (
    <div ref={ref} className="flex gap-1.5 overflow-x-auto px-3 py-1.5 border-t border-surface-700 bg-surface-950 scrollbar-none shrink-0">
      {events.slice(-40).map((e) => (
        <span
          key={e.id}
          className="shrink-0 text-[10px] font-mono px-2 py-0.5 rounded-full"
          style={{ background: topicColor(e.topic) + "20", color: topicColor(e.topic), border: `1px solid ${topicColor(e.topic)}40` }}
        >
          {e.topic.split(".").slice(0, 2).join(".")}
          {e.payload?.symbol ? ` · ${e.payload.symbol}` : ""}
        </span>
      ))}
      {events.length === 0 && <span className="text-[10px] text-content-muted">Waiting for events…</span>}
    </div>
  );
}

// ── Detail drawer ─────────────────────────────────────────────────────────────

type DetailTab = "plan" | "journal" | "tasks" | "trades" | "audit" | "triage" | "trace";

function DetailDrawer({ pmId, events, onClose }: { pmId: string; events: PMEvent[]; onClose: () => void }) {
  const [tab, setTab] = useState<DetailTab>("plan");

  const { data: state } = useQuery<PMState>({ queryKey: ["pm_state", pmId], queryFn: () => api.pmState(pmId), refetchInterval: 15000 });
  const { data: audit } = useQuery<AuditEntry[]>({ queryKey: ["pm_audit", pmId], queryFn: () => api.pmAudit(pmId), enabled: tab === "audit", refetchInterval: 10000 });
  const { data: triage } = useQuery<TriageDecision[]>({ queryKey: ["pm_triage", pmId], queryFn: () => api.pmTriageLog(pmId), enabled: tab === "triage", refetchInterval: 5000 });
  const { data: trades } = useQuery<Trade[]>({ queryKey: ["pm_trades", pmId], queryFn: () => api.pmTrades(pmId), enabled: tab === "trades", refetchInterval: 10000 });

  const TABS: { id: DetailTab; label: string }[] = [
    { id: "plan", label: "Plan" }, { id: "journal", label: "Journal" },
    { id: "tasks", label: "Tasks" }, { id: "trades", label: "Trades" },
    { id: "audit", label: "Audit" }, { id: "triage", label: "Triage" },
    { id: "trace", label: "Trace" },
  ];

  const relevant = events.filter((e) => e.pm_id === pmId || e.topic.includes(`.${pmId}`)).slice(-50);

  return (
    <div className="w-80 shrink-0 border-l border-surface-700 bg-surface-900 flex flex-col overflow-hidden animate-slide-up">
      <div className="flex items-center justify-between px-3 py-2 border-b border-surface-700 shrink-0">
        <span className="text-sm font-bold text-content-primary">PM{pmId}</span>
        <button onClick={onClose} className="text-content-muted hover:text-content-primary text-xs">✕</button>
      </div>

      {/* Tabs */}
      <div className="flex gap-0.5 px-2 pt-1.5 border-b border-surface-700 overflow-x-auto scrollbar-none shrink-0">
        {TABS.map((t) => (
          <button
            key={t.id}
            onClick={() => setTab(t.id)}
            className={cn(
              "px-2 py-1 text-[10px] rounded-t transition-colors shrink-0 uppercase tracking-wide",
              tab === t.id ? "bg-surface-700 text-content-primary" : "text-content-muted hover:text-content-primary"
            )}
          >
            {t.label}
          </button>
        ))}
      </div>

      <div className="flex-1 overflow-y-auto p-3 text-xs font-mono">
        {tab === "plan" && <pre className="whitespace-pre-wrap text-content-primary leading-relaxed">{state?.plan || "(no plan yet)"}</pre>}
        {tab === "journal" && <pre className="whitespace-pre-wrap text-content-primary leading-relaxed">{state?.journal || "(no journal)"}</pre>}
        {tab === "tasks" && (
          <div className="space-y-3">
            {["backlog", "in_progress", "done"].map((col) => (
              <div key={col}>
                <div className="text-[10px] text-content-muted uppercase tracking-widest mb-1">{col.replace("_", " ")}</div>
                {((state?.tasks as Record<string, unknown[]>)?.[col] ?? []).map((t, i) => (
                  <div key={i} className="bg-surface-800 rounded px-2 py-1 mb-1 text-content-primary text-[11px]">
                    {typeof t === "string" ? t : JSON.stringify(t)}
                  </div>
                ))}
              </div>
            ))}
          </div>
        )}
        {tab === "trades" && (
          <table className="w-full text-[10px]">
            <thead><tr className="text-content-muted border-b border-surface-700">
              <th className="text-left py-1">Symbol</th><th className="text-right py-1">Entry</th>
              <th className="text-right py-1">P&L</th><th className="text-left py-1">Outcome</th>
            </tr></thead>
            <tbody>
              {(trades ?? []).map((t, i) => (
                <tr key={i} className="border-b border-surface-700/30">
                  <td className="py-1 text-content-primary">{t.symbol}</td>
                  <td className="py-1 text-right text-content-muted">{fmt.inr(t.entry_price)}</td>
                  <td className={cn("py-1 text-right num", (t.pnl_inr ?? 0) >= 0 ? "positive" : "negative")}>
                    {t.pnl_inr != null ? fmt.inr(t.pnl_inr) : "open"}
                  </td>
                  <td className="py-1 text-content-muted">{t.outcome ?? "open"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {tab === "audit" && (
          <div className="space-y-1">
            {(audit ?? []).slice().reverse().map((e, i) => (
              <div key={i} className={cn("rounded px-2 py-1 text-[10px]",
                e.event === "ORDER_BLOCKED" || e.event === "DAILY_HALT" ? "bg-accent-danger/10 text-accent-danger" :
                e.event === "ORDER_PLACED" ? "bg-accent-success/10 text-accent-success" :
                "bg-surface-800 text-content-muted"
              )}>
                <span className="text-content-muted mr-2">{e.ts.slice(11, 19)}</span>
                <span className="font-bold">{e.event}</span>
              </div>
            ))}
          </div>
        )}
        {tab === "triage" && (
          <div className="space-y-1">
            {(triage ?? []).slice().reverse().map((d, i) => (
              <div key={i} className="flex items-center gap-2 border-b border-surface-700/30 py-1">
                <span className="text-content-muted w-16 shrink-0">{d.ts.slice(11, 19)}</span>
                <span className="px-1 rounded text-[10px] shrink-0" style={{ background: topicColor(d.topic) + "20", color: topicColor(d.topic) }}>
                  {d.classification}
                </span>
                <span className="text-content-primary truncate">{d.topic}</span>
              </div>
            ))}
          </div>
        )}
        {tab === "trace" && (
          <div className="space-y-1">
            {relevant.map((e) => (
              <div key={e.id} className="flex items-center gap-2 border-b border-surface-700/30 py-1">
                <span className="w-2 h-2 rounded-full shrink-0" style={{ background: topicColor(e.topic) }} />
                <span className="text-content-muted w-16 shrink-0">{e.ts.slice(11, 19)}</span>
                <span className="text-content-primary truncate">{e.topic}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

// ── ReactFlow graph ───────────────────────────────────────────────────────────

function buildGraph(pm: PMSummary | null): { nodes: Node[]; edges: Edge[] } {
  if (!pm) return { nodes: [], edges: [] };
  const id = pm.pm_id;
  const s = { background: "#111827", border: "1px solid #1f2937", color: "#9ca3af", fontSize: 11, borderRadius: 8, padding: "6px 10px", textAlign: "center" as const };

  const nodes: Node[] = [
    { id: "tier1",          data: { label: "Tier 1 Publishers" }, position: { x: 280, y: 20 },  style: { ...s, width: 140 } },
    { id: "events_db",      data: { label: "events.db" },         position: { x: 300, y: 110 }, style: { ...s, border: "1px solid #3b82f6", color: "#3b82f6", width: 100 } },
    { id: `pm_${id}`,       data: { label: `PM${id} (Strategic)` }, position: { x: 280, y: 210 }, style: { ...s, border: "2px solid #8b5cf6", color: "#c4b5fd", fontWeight: "bold", width: 130 } },
    { id: `triage_${id}`,   data: { label: `Triage` },            position: { x: 80, y: 330 },  style: { ...s, border: "1px solid #f59e0b", color: "#fcd34d", width: 90 } },
    { id: `trader_${id}`,   data: { label: `Trader` },            position: { x: 280, y: 330 }, style: { ...s, border: "1px solid #10b981", color: "#6ee7b7", width: 90 } },
    { id: `risk_${id}`,     data: { label: `Risk` },              position: { x: 480, y: 330 }, style: { ...s, border: "1px solid #ef4444", color: "#fca5a5", width: 90 } },
    { id: "broker",         data: { label: "Broker" },            position: { x: 280, y: 440 }, style: { ...s, width: 90 } },
  ];

  const es = { style: { stroke: "#374151" }, markerEnd: { type: MarkerType.ArrowClosed, color: "#374151" } };
  const edges: Edge[] = [
    { id: "e1", source: "tier1",        target: "events_db",    ...es },
    { id: "e2", source: "events_db",    target: `triage_${id}`, ...es },
    { id: "e3", source: `triage_${id}`, target: `pm_${id}`,     label: "wakeup", style: { stroke: "#8b5cf6" }, markerEnd: { type: MarkerType.ArrowClosed } },
    { id: "e4", source: `triage_${id}`, target: `trader_${id}`, label: "exec",   style: { stroke: "#f97316" }, markerEnd: { type: MarkerType.ArrowClosed } },
    { id: "e5", source: `pm_${id}`,     target: `trader_${id}`, label: "order",  style: { stroke: "#10b981" }, markerEnd: { type: MarkerType.ArrowClosed } },
    { id: "e6", source: `pm_${id}`,     target: `risk_${id}`,   label: "check",  style: { stroke: "#ef4444" }, markerEnd: { type: MarkerType.ArrowClosed } },
    { id: "e7", source: `trader_${id}`, target: "broker",       ...es },
    { id: "e8", source: `risk_${id}`,   target: `pm_${id}`,     label: "breach", style: { stroke: "#ef4444", strokeDasharray: "4" }, markerEnd: { type: MarkerType.ArrowClosed } },
  ];

  return { nodes, edges };
}

// ── Main page ─────────────────────────────────────────────────────────────────

export function PMs() {
  const [selectedPmId, setSelectedPmId] = useState<string | null>(null);
  const [liveEvents, setLiveEvents] = useState<PMEvent[]>([]);
  const [activeEdges, setActiveEdges] = useState<Set<string>>(new Set());
  const [pmTrades, setPmTrades] = useState<Record<string, Trade[]>>({});
  const wsRef = useRef<WebSocket | null>(null);

  const { data: pms } = useQuery<PMSummary[]>({ queryKey: ["pms"], queryFn: api.pms, refetchInterval: 10000 });

  // Load trades for equity race
  useEffect(() => {
    if (!pms) return;
    pms.forEach((pm) => {
      api.pmTrades(pm.pm_id).then((t) => setPmTrades((prev) => ({ ...prev, [pm.pm_id]: t })));
    });
  }, [pms]);

  const selectedPm = pms?.find((p) => p.pm_id === selectedPmId) ?? null;
  const { nodes: initNodes, edges: initEdges } = buildGraph(selectedPm);
  const [nodes, , onNodesChange] = useNodesState(initNodes);
  const [edges, , onEdgesChange] = useEdgesState(initEdges);

  const animateEdge = useCallback((edgeId: string) => {
    setActiveEdges((prev) => new Set([...prev, edgeId]));
    setTimeout(() => setActiveEdges((prev) => { const n = new Set(prev); n.delete(edgeId); return n; }), 1500);
  }, []);

  const handleEvent = useCallback((event: PMEvent) => {
    setLiveEvents((prev) => [...prev.slice(-500), event]);
    if (!selectedPmId) return;
    const id = selectedPmId, t = event.topic;
    if (t.startsWith("price.spike") || t.startsWith("news")) animateEdge("e1");
    if (t === `pm.wakeup.${id}`) animateEdge("e3");
    if (t === `exec_order.${id}`) { animateEdge("e4"); animateEdge("e5"); }
    if (t === `fill.${id}`) animateEdge("e7");
    if (t === `risk.breach.${id}`) { animateEdge("e6"); animateEdge("e8"); }
  }, [selectedPmId, animateEdge]);

  useEffect(() => {
    const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    const ws = new WebSocket(`${proto}//${window.location.host}/ws/pm_events`);
    wsRef.current = ws;
    ws.onmessage = (e) => {
      try {
        const msg = JSON.parse(e.data);
        if (msg.type === "pm_event") handleEvent(msg.event as PMEvent);
      } catch {}
    };
    return () => ws.close();
  }, [handleEvent]);

  const animatedEdges = edges.map((e) => ({
    ...e, animated: activeEdges.has(e.id),
    style: { ...e.style, strokeWidth: activeEdges.has(e.id) ? 2 : 1 },
  }));

  // Sort PMs by P&L descending for leaderboard
  const sortedPms = [...(pms ?? [])].sort((a, b) => b.daily_pnl_inr - a.daily_pnl_inr);

  return (
    <div className="flex flex-col h-full overflow-hidden bg-surface-900">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-2 border-b border-surface-700 bg-surface-950 shrink-0">
        <div className="flex items-center gap-3">
          <span className="text-sm font-bold text-content-primary">Portfolio Managers</span>
          <Badge variant="info" dot>{pms?.length ?? 0} active</Badge>
        </div>
        <KillSwitch />
      </div>

      {/* Body */}
      <div className="flex flex-1 overflow-hidden">
        {/* Left: Leaderboard */}
        <div className="w-52 shrink-0 border-r border-surface-700 flex flex-col overflow-hidden">
          <div className="panel-header border-b border-surface-700">
            <span className="panel-title">Leaderboard</span>
          </div>
          <div className="flex-1 overflow-y-auto p-2 flex flex-col gap-2">
            {sortedPms.map((pm, i) => (
              <PMCard
                key={pm.pm_id}
                pm={pm}
                rank={i + 1}
                selected={selectedPmId === pm.pm_id}
                color={PM_COLORS[i % PM_COLORS.length]}
                onClick={() => setSelectedPmId(selectedPmId === pm.pm_id ? null : pm.pm_id)}
              />
            ))}
            {sortedPms.length === 0 && (
              <div className="text-xs text-content-muted p-2">No PMs registered yet.</div>
            )}
          </div>
        </div>

        {/* Center: Flow + equity race */}
        <div className="flex-1 flex flex-col overflow-hidden">
          {/* Equity race chart */}
          <div className="h-36 border-b border-surface-700 p-2 shrink-0">
            <div className="panel-title mb-1 px-1">Equity Race</div>
            <EquityRace pms={pms ?? []} trades={pmTrades} />
          </div>

          {/* ReactFlow */}
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
                <Background color="#1f2937" gap={20} />
                <Controls />
              </ReactFlow>
            ) : (
              <div className="flex items-center justify-center h-full text-xs text-content-muted">
                Select a PM to see its agent flow
              </div>
            )}
          </div>

          {/* Event ticker */}
          <EventTicker events={liveEvents} />
        </div>

        {/* Right: Detail drawer */}
        {selectedPmId && (
          <DetailDrawer
            pmId={selectedPmId}
            events={liveEvents}
            onClose={() => setSelectedPmId(null)}
          />
        )}
      </div>
    </div>
  );
}
