import { useState, useCallback, useRef } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { cn } from "@/lib/cn";
import { fmt } from "@/lib/formatters";
import { Badge } from "@/components/ui/Badge";
import { AnimatedNumber } from "@/components/ui/AnimatedNumber";
import { ThoughtStream, type StreamEvent } from "@/components/pm/ThoughtStream";
import { PMAgentGraph } from "@/components/pm/PMAgentGraph";
import { PositionCard } from "@/components/pm/PositionCard";
import {
  AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer, ReferenceLine,
} from "recharts";
import {
  ArrowLeft, Pause, Play, ShieldOff, Shield, TrendingUp, TrendingDown,
  Briefcase, Activity, Clock,
} from "lucide-react";

// ── PM selector strip (left) ──────────────────────────────────────────────────

function PMStrip({ activePmId }: { activePmId: string }) {
  const navigate = useNavigate();
  const { data: pms = [] } = useQuery({ queryKey: ["pms"], queryFn: api.pms, refetchInterval: 10000 });
  const COLORS = ["#3b82f6", "#10b981", "#f59e0b", "#8b5cf6", "#ef4444"];

  return (
    <div className="w-14 border-r border-surface-700 bg-surface-950 flex flex-col items-center py-3 gap-2 shrink-0">
      <button onClick={() => navigate("/pms")} className="text-content-muted hover:text-content-primary mb-2">
        <ArrowLeft size={14} />
      </button>
      {pms.map((pm, i) => {
        const up = pm.daily_pnl_inr >= 0;
        const active = pm.pm_id === activePmId;
        return (
          <button
            key={pm.pm_id}
            onClick={() => navigate(`/pms/${pm.pm_id}`)}
            title={`PM${pm.pm_id}`}
            className={cn(
              "w-9 h-9 rounded-xl flex items-center justify-center text-xs font-bold transition-all border",
              active
                ? "border-2 scale-110"
                : "border-surface-700 text-content-muted hover:border-surface-600"
            )}
            style={active ? { borderColor: COLORS[i % COLORS.length], color: COLORS[i % COLORS.length], boxShadow: `0 0 10px ${COLORS[i % COLORS.length]}44` } : {}}
          >
            {pm.pm_id}
          </button>
        );
      })}
    </div>
  );
}

// ── Identity bar ──────────────────────────────────────────────────────────────

function IdentityBar({ pmId }: { pmId: string }) {
  const qc = useQueryClient();
  const { data: pms = [] } = useQuery({ queryKey: ["pms"], queryFn: api.pms, refetchInterval: 10000 });
  const { data: pauseData } = useQuery({ queryKey: ["pm_paused", pmId], queryFn: () => api.pmPausedStatus(pmId), refetchInterval: 5000 });
  const { data: ks } = useQuery({ queryKey: ["kill_switch"], queryFn: api.killSwitchStatus, refetchInterval: 5000 });

  const pause  = useMutation({ mutationFn: () => api.pmPause(pmId),  onSuccess: () => qc.invalidateQueries({ queryKey: ["pm_paused", pmId] }) });
  const resume = useMutation({ mutationFn: () => api.pmResume(pmId), onSuccess: () => qc.invalidateQueries({ queryKey: ["pm_paused", pmId] }) });
  const ksOn   = useMutation({ mutationFn: () => api.killSwitchOn(),  onSuccess: () => qc.invalidateQueries({ queryKey: ["kill_switch"] }) });
  const ksOff  = useMutation({ mutationFn: () => api.killSwitchOff(), onSuccess: () => qc.invalidateQueries({ queryKey: ["kill_switch"] }) });

  const pm = pms.find((p) => p.pm_id === pmId);
  const paused = pauseData?.paused ?? false;
  const ksActive = ks?.active ?? false;
  const up = (pm?.daily_pnl_inr ?? 0) >= 0;

  return (
    <div className="flex items-center gap-4 px-4 py-2.5 border-b border-surface-700 bg-surface-950 shrink-0">
      <div className="flex items-center gap-2">
        <span className="text-base font-bold text-content-primary">PM{pmId}</span>
        {paused
          ? <Badge variant="warning" dot>Paused</Badge>
          : <Badge variant="success" dot>Live</Badge>
        }
      </div>

      {/* Stats */}
      <div className="flex items-center gap-5 ml-2">
        <div className="flex flex-col">
          <span className="text-[10px] text-content-muted">Daily P&L</span>
          <span className={cn("num text-sm font-bold", up ? "positive" : "negative")}>
            {up ? "+" : ""}<AnimatedNumber value={pm?.daily_pnl_inr ?? 0} format={(v) => fmt.inr(v)} />
          </span>
        </div>
        <div className="flex flex-col">
          <span className="text-[10px] text-content-muted">Positions</span>
          <span className="num text-sm font-bold text-content-primary">{pm?.open_positions ?? 0}</span>
        </div>
        <div className="flex flex-col">
          <span className="text-[10px] text-content-muted">Capital</span>
          <span className="num text-sm text-content-secondary">{fmt.inr(pm?.capital ?? 0)}</span>
        </div>
        <div className="flex flex-col">
          <span className="text-[10px] text-content-muted">Last Wakeup</span>
          <span className="text-xs text-content-muted flex items-center gap-1">
            <Clock size={10} />
            {pm?.last_wakeup ? new Date(pm.last_wakeup + "Z").toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit" }) : "—"}
          </span>
        </div>
      </div>

      <div className="ml-auto flex items-center gap-2">
        <button
          onClick={() => paused ? resume.mutate() : pause.mutate()}
          className={cn("btn-sm", paused ? "btn-success" : "btn-ghost")}
        >
          {paused ? <><Play size={11} /> Resume</> : <><Pause size={11} /> Pause</>}
        </button>
        <button
          onClick={() => ksActive ? ksOff.mutate() : ksOn.mutate()}
          className={cn("btn-sm", ksActive ? "btn-danger animate-pulse-glow" : "btn-ghost")}
        >
          {ksActive ? <><ShieldOff size={11} /> Kill Switch ON</> : <><Shield size={11} /> Kill Switch</>}
        </button>
      </div>
    </div>
  );
}

// ── Equity curve ──────────────────────────────────────────────────────────────

function EquityCurve({ pmId }: { pmId: string }) {
  const { data = [] } = useQuery({
    queryKey: ["equity_today", pmId],
    queryFn: () => api.pmEquityToday(pmId),
    refetchInterval: 15000,
  });

  const isPositive = (data[data.length - 1]?.cum_pnl ?? 0) >= 0;

  if (!data.length) return (
    <div className="flex items-center justify-center h-full text-xs text-content-muted">No trades today</div>
  );

  return (
    <ResponsiveContainer width="100%" height="100%">
      <AreaChart data={data} margin={{ top: 4, right: 12, bottom: 0, left: 0 }}>
        <defs>
          <linearGradient id={`eq-${pmId}`} x1="0" y1="0" x2="0" y2="1">
            <stop offset="5%" stopColor={isPositive ? "#10b981" : "#ef4444"} stopOpacity={0.3} />
            <stop offset="95%" stopColor={isPositive ? "#10b981" : "#ef4444"} stopOpacity={0} />
          </linearGradient>
        </defs>
        <XAxis dataKey="ts" hide />
        <YAxis tick={{ fill: "#6b7280", fontSize: 9 }} tickLine={false} axisLine={false} tickFormatter={(v) => "₹" + v} width={40} />
        <ReferenceLine y={0} stroke="#374151" strokeDasharray="3 3" />
        <Tooltip
          contentStyle={{ background: "#111827", border: "1px solid #1f2937", borderRadius: 8, fontSize: 11 }}
          formatter={(v: number, _: string, props: any) => [
            `${fmt.inr(v)} (${props.payload.symbol})`,
            "Cum P&L",
          ]}
        />
        <Area
          type="monotone"
          dataKey="cum_pnl"
          stroke={isPositive ? "#10b981" : "#ef4444"}
          fill={`url(#eq-${pmId})`}
          strokeWidth={1.5}
          dot={(props: any) => (
            <circle key={props.index} cx={props.cx} cy={props.cy} r={3}
              fill={props.payload.pnl >= 0 ? "#10b981" : "#ef4444"}
              stroke="#111827" strokeWidth={1} />
          )}
        />
      </AreaChart>
    </ResponsiveContainer>
  );
}

// ── Main cockpit ──────────────────────────────────────────────────────────────

export function PMCockpit() {
  const { pmId } = useParams<{ pmId: string }>();
  const [lastEvent, setLastEvent] = useState<StreamEvent | null>(null);

  const { data: state } = useQuery({
    queryKey: ["pm_state", pmId],
    queryFn: () => api.pmState(pmId!),
    refetchInterval: 30000,
    enabled: !!pmId,
  });

  const { data: trades = [] } = useQuery({
    queryKey: ["pm_trades", pmId],
    queryFn: () => api.pmTrades(pmId!),
    refetchInterval: 15000,
    enabled: !!pmId,
  });

  const handleEvent = useCallback((ev: StreamEvent) => setLastEvent(ev), []);

  if (!pmId) return null;

  const positions = (state?.positions ?? []) as any[];
  const recentTrades = trades.slice(0, 8);

  return (
    <div className="flex h-full overflow-hidden bg-surface-900">
      {/* PM selector strip */}
      <PMStrip activePmId={pmId} />

      {/* Main cockpit */}
      <div className="flex-1 flex flex-col overflow-hidden">
        {/* Identity bar */}
        <IdentityBar pmId={pmId} />

        {/* 3-column body */}
        <div className="flex-1 flex overflow-hidden">

          {/* LEFT: Agent graph + Plan */}
          <div className="w-72 border-r border-surface-700 flex flex-col shrink-0 overflow-hidden">
            {/* Agent graph */}
            <div className="h-52 border-b border-surface-700 shrink-0">
              <div className="panel-header border-b border-surface-700">
                <span className="panel-title flex items-center gap-1.5">
                  <Activity size={11} /> Agent Pipeline
                </span>
              </div>
              <div className="h-[calc(100%-33px)]">
                <PMAgentGraph lastEvent={lastEvent} />
              </div>
            </div>

            {/* Current plan */}
            <div className="flex-1 overflow-hidden flex flex-col">
              <div className="panel-header border-b border-surface-700 shrink-0">
                <span className="panel-title">Current Plan</span>
              </div>
              <div className="flex-1 overflow-y-auto p-3">
                <pre className="text-[11px] text-content-secondary font-sans whitespace-pre-wrap leading-relaxed">
                  {state?.plan || "No plan yet."}
                </pre>
              </div>
            </div>
          </div>

          {/* CENTER: Thought stream */}
          <div className="flex-1 flex flex-col overflow-hidden border-r border-surface-700">
            <ThoughtStream pmId={pmId} onEvent={handleEvent} />
          </div>

          {/* RIGHT: Positions + Recent trades */}
          <div className="w-64 flex flex-col shrink-0 overflow-hidden">
            {/* Open positions */}
            <div className="flex-1 overflow-hidden flex flex-col border-b border-surface-700">
              <div className="panel-header border-b border-surface-700 shrink-0">
                <span className="panel-title flex items-center gap-1.5">
                  <Briefcase size={11} /> Open Positions
                </span>
                <Badge variant="default">{positions.length}</Badge>
              </div>
              <div className="flex-1 overflow-y-auto p-2 flex flex-col gap-2">
                {positions.length === 0 && (
                  <div className="flex items-center justify-center h-full text-xs text-content-muted">No open positions</div>
                )}
                {positions.map((pos: any, i: number) => (
                  <PositionCard key={pos.symbol ?? i} pos={pos} />
                ))}
              </div>
            </div>

            {/* Recent trades */}
            <div className="h-56 flex flex-col overflow-hidden shrink-0">
              <div className="panel-header border-b border-surface-700 shrink-0">
                <span className="panel-title">Recent Trades</span>
              </div>
              <div className="flex-1 overflow-y-auto">
                {recentTrades.length === 0 && (
                  <div className="flex items-center justify-center h-full text-xs text-content-muted">No trades yet</div>
                )}
                {recentTrades.map((t: any, i: number) => {
                  const up = (t.pnl_inr ?? 0) >= 0;
                  return (
                    <div key={i} className={cn("flex items-center justify-between px-3 py-1.5 border-b border-surface-700/50 border-l-2", up ? "border-l-accent-success" : "border-l-accent-danger")}>
                      <div>
                        <div className="text-xs font-semibold text-content-primary">{t.symbol}</div>
                        <div className="text-[10px] text-content-muted">{t.exit_reason ?? t.outcome}</div>
                      </div>
                      <span className={cn("num text-xs font-bold", up ? "positive" : "negative")}>
                        {up ? "+" : ""}{fmt.inr(t.pnl_inr)}
                      </span>
                    </div>
                  );
                })}
              </div>
            </div>
          </div>
        </div>

        {/* BOTTOM: Equity curve */}
        <div className="h-28 border-t border-surface-700 shrink-0 flex flex-col">
          <div className="panel-header border-b border-surface-700 shrink-0">
            <span className="panel-title flex items-center gap-1.5">
              <TrendingUp size={11} /> Today's Equity Curve
            </span>
          </div>
          <div className="flex-1">
            <EquityCurve pmId={pmId} />
          </div>
        </div>
      </div>
    </div>
  );
}
