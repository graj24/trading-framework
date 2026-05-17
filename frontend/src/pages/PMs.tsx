import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { api, PMSummary } from "@/lib/api";
import { Badge } from "@/components/ui/Badge";
import { cn } from "@/lib/cn";
import { fmt } from "@/lib/formatters";
import { Pause, Play, ShieldOff, Shield, TrendingUp, TrendingDown, ArrowRight } from "lucide-react";

const PM_COLORS = ["#3b82f6", "#10b981", "#f59e0b", "#8b5cf6", "#ef4444", "#06b6d4"];

function KillSwitch() {
  const qc = useQueryClient();
  const { data } = useQuery({ queryKey: ["kill_switch"], queryFn: api.killSwitchStatus, refetchInterval: 5000 });
  const on  = useMutation({ mutationFn: () => api.killSwitchOn(),  onSuccess: () => qc.invalidateQueries({ queryKey: ["kill_switch"] }) });
  const off = useMutation({ mutationFn: () => api.killSwitchOff(), onSuccess: () => qc.invalidateQueries({ queryKey: ["kill_switch"] }) });
  const active = data?.active ?? false;

  return (
    <button
      onClick={() => active ? off.mutate() : on.mutate()}
      className={cn("btn-sm", active ? "btn-danger animate-pulse-glow" : "btn-ghost")}
    >
      {active ? <><ShieldOff size={12} /> Kill Switch ON</> : <><Shield size={12} /> Kill Switch</>}
    </button>
  );
}

function PMCard({ pm, rank, color }: { pm: PMSummary; rank: number; color: string }) {
  const navigate = useNavigate();
  const qc = useQueryClient();
  const { data: pauseData } = useQuery({ queryKey: ["pm_paused", pm.pm_id], queryFn: () => api.pmPausedStatus(pm.pm_id), refetchInterval: 5000 });
  const pause  = useMutation({ mutationFn: () => api.pmPause(pm.pm_id),  onSuccess: () => qc.invalidateQueries({ queryKey: ["pm_paused", pm.pm_id] }) });
  const resume = useMutation({ mutationFn: () => api.pmResume(pm.pm_id), onSuccess: () => qc.invalidateQueries({ queryKey: ["pm_paused", pm.pm_id] }) });
  const paused = pauseData?.paused ?? false;
  const up = pm.daily_pnl_inr >= 0;

  return (
    <div
      className="panel p-4 flex flex-col gap-3 cursor-pointer hover:brightness-110 transition-all border-l-2 group"
      style={{ borderLeftColor: color }}
      onClick={() => navigate(`/pms/${pm.pm_id}`)}
    >
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <div className="w-7 h-7 rounded-lg flex items-center justify-center text-xs font-bold"
            style={{ background: color + "20", color }}>
            {rank}
          </div>
          <span className="font-bold text-content-primary">PM{pm.pm_id}</span>
          {paused
            ? <Badge variant="warning" dot>Paused</Badge>
            : <Badge variant="success" dot>Live</Badge>
          }
        </div>
        <ArrowRight size={14} className="text-content-muted group-hover:text-content-primary transition-colors" />
      </div>

      {/* P&L */}
      <div className="flex items-end gap-2">
        {up ? <TrendingUp size={18} className="text-accent-success mb-0.5" /> : <TrendingDown size={18} className="text-accent-danger mb-0.5" />}
        <span className={cn("num text-2xl font-bold", up ? "positive" : "negative")}>
          {up ? "+" : ""}{fmt.inr(pm.daily_pnl_inr)}
        </span>
      </div>

      {/* Stats row */}
      <div className="flex gap-4 text-xs text-content-muted">
        <span>{pm.open_positions} open</span>
        <span>{pm.inbox_count} inbox</span>
        <span className="num">{fmt.inr(pm.capital)} capital</span>
      </div>

      {/* Pause button */}
      <button
        onClick={(e) => { e.stopPropagation(); paused ? resume.mutate() : pause.mutate(); }}
        className={cn("btn-sm w-full justify-center", paused ? "btn-success" : "btn-ghost")}
      >
        {paused ? <><Play size={11} /> Resume</> : <><Pause size={11} /> Pause</>}
      </button>
    </div>
  );
}

export function PMs() {
  const { data: pms = [] } = useQuery({ queryKey: ["pms"], queryFn: api.pms, refetchInterval: 10000 });
  const sorted = [...pms].sort((a, b) => b.daily_pnl_inr - a.daily_pnl_inr);

  return (
    <div className="flex flex-col h-full bg-surface-900 overflow-auto">
      {/* Header */}
      <div className="flex items-center justify-between px-6 py-4 border-b border-surface-700 shrink-0">
        <div>
          <h1 className="text-sm font-bold text-content-primary">Portfolio Managers</h1>
          <p className="text-xs text-content-muted mt-0.5">Click any PM to open their live cockpit</p>
        </div>
        <KillSwitch />
      </div>

      {/* PM grid */}
      <div className="flex-1 p-6">
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
          {sorted.map((pm, i) => (
            <PMCard key={pm.pm_id} pm={pm} rank={i + 1} color={PM_COLORS[i % PM_COLORS.length]} />
          ))}
          {pms.length === 0 && (
            <div className="col-span-full flex items-center justify-center h-40 text-content-muted text-sm">
              No PMs registered. Run <code className="mx-1 px-1.5 py-0.5 bg-surface-800 rounded font-mono text-xs">python scripts/register_pm.py</code> to add one.
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
