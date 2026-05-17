import { useState } from "react";
import { streamBacktest } from "@/lib/api";
import { fmt } from "@/lib/formatters";
import { AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer, ReferenceLine } from "recharts";
import { Badge } from "@/components/ui/Badge";
import { cn } from "@/lib/cn";
import { Play, RefreshCw } from "lucide-react";

interface Summary { trades: number; win_rate: number; net_pnl: number }
interface TradeRow { symbol: string; pnl_inr?: number; exit_reason?: string; entry_date?: string }

function MetricTile({ label, value, variant }: { label: string; value: string; variant?: "success" | "danger" | "default" }) {
  return (
    <div className="panel p-3 flex flex-col gap-1 flex-1">
      <div className="stat-label">{label}</div>
      <div className={cn("num text-xl font-bold",
        variant === "success" ? "text-accent-success" :
        variant === "danger"  ? "text-accent-danger" :
        "text-content-primary"
      )}>
        {value}
      </div>
    </div>
  );
}

export function Backtest() {
  const [strategy, setStrategy] = useState<"gap" | "intraday">("gap");
  const [gapThreshold, setGapThreshold] = useState(2.0);
  const [slPct, setSlPct] = useState(1.5);
  const [targetPct, setTargetPct] = useState(3.0);
  const [running, setRunning] = useState(false);
  const [progress, setProgress] = useState(0);
  const [trades, setTrades] = useState<TradeRow[]>([]);
  const [summary, setSummary] = useState<Summary | null>(null);
  const [equityData, setEquityData] = useState<{ i: number; pnl: number }[]>([]);

  const run = async () => {
    setRunning(true); setTrades([]); setSummary(null); setEquityData([]); setProgress(0);
    const endpoint = strategy === "gap" ? "/api/backtest/gap" : "/api/backtest/intraday";
    let cum = 0, idx = 0;
    try {
      for await (const event of streamBacktest(endpoint, { gap_threshold: gapThreshold, sl_pct: slPct, target_pct: targetPct })) {
        if (event.type === "progress") setProgress(event.pct as number);
        else if (event.type === "trade") {
          const t = event as unknown as TradeRow;
          setTrades((prev) => [...prev, t]);
          cum += t.pnl_inr ?? 0; idx++;
          setEquityData((prev) => [...prev, { i: idx, pnl: Math.round(cum * 100) / 100 }]);
        } else if (event.type === "summary") {
          setSummary(event as unknown as Summary); setProgress(100);
        }
      }
    } finally { setRunning(false); }
  };

  const isPositive = (summary?.net_pnl ?? 0) >= 0;

  return (
    <div className="flex h-full overflow-hidden bg-surface-900">
      {/* Controls sidebar */}
      <div className="w-56 border-r border-surface-700 bg-surface-950 flex flex-col gap-4 p-4 shrink-0 overflow-auto">
        <div className="panel-title">Strategy</div>
        <div className="flex flex-col gap-1.5">
          {(["gap", "intraday"] as const).map((s) => (
            <button
              key={s}
              onClick={() => setStrategy(s)}
              className={cn("text-xs px-3 py-2 rounded-lg border text-left transition-colors",
                strategy === s
                  ? "bg-accent-primary/15 border-accent-primary/40 text-accent-primary"
                  : "border-surface-700 text-content-muted hover:border-surface-600"
              )}
            >
              {s === "gap" ? "📈 Gap Strategy" : "⚡ Intraday ML"}
            </button>
          ))}
        </div>

        <div className="border-t border-surface-700 pt-3 flex flex-col gap-4">
          {[
            { label: "Gap Threshold", val: gapThreshold, set: setGapThreshold, min: 0.5, max: 10, step: 0.5 },
            { label: "Stop Loss %",   val: slPct,         set: setSlPct,         min: 0.5, max: 5,  step: 0.5 },
            { label: "Target %",      val: targetPct,     set: setTargetPct,     min: 1,   max: 10, step: 0.5 },
          ].map(({ label, val, set, min, max, step }) => (
            <div key={label} className="flex flex-col gap-1.5">
              <div className="flex justify-between">
                <span className="text-xs text-content-muted">{label}</span>
                <span className="num text-xs text-content-secondary">{val}%</span>
              </div>
              <input type="range" min={min} max={max} step={step} value={val}
                onChange={(e) => set(parseFloat(e.target.value))}
                className="w-full accent-blue h-1" />
            </div>
          ))}
        </div>

        <button
          onClick={run}
          disabled={running}
          className={cn("mt-auto btn-md w-full justify-center", running ? "btn-ghost" : "btn-primary")}
        >
          {running ? <><RefreshCw size={13} className="animate-spin" /> {progress}%</> : <><Play size={13} /> Run Backtest</>}
        </button>
      </div>

      {/* Results */}
      <div className="flex-1 flex flex-col overflow-hidden">
        {/* Metric tiles */}
        {summary && (
          <div className="flex gap-3 p-3 border-b border-surface-700 shrink-0">
            <MetricTile label="Total Trades" value={String(summary.trades)} />
            <MetricTile label="Win Rate" value={summary.win_rate.toFixed(1) + "%"} variant={summary.win_rate >= 50 ? "success" : "danger"} />
            <MetricTile label="Net P&L" value={fmt.inr(summary.net_pnl)} variant={isPositive ? "success" : "danger"} />
          </div>
        )}

        {/* Equity curve */}
        {equityData.length > 0 && (
          <div className="h-44 border-b border-surface-700 shrink-0 p-3">
            <div className="panel-title mb-2">Equity Curve</div>
            <ResponsiveContainer width="100%" height="85%">
              <AreaChart data={equityData} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
                <defs>
                  <linearGradient id="btGrad" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor={isPositive ? "#10b981" : "#ef4444"} stopOpacity={0.25} />
                    <stop offset="95%" stopColor={isPositive ? "#10b981" : "#ef4444"} stopOpacity={0} />
                  </linearGradient>
                </defs>
                <XAxis dataKey="i" hide />
                <YAxis tick={{ fill: "#6b7280", fontSize: 9 }} tickLine={false} axisLine={false} tickFormatter={(v) => "₹" + v} width={40} />
                <ReferenceLine y={0} stroke="#374151" strokeDasharray="3 3" />
                <Tooltip contentStyle={{ background: "#111827", border: "1px solid #1f2937", borderRadius: 8, fontSize: 11 }} formatter={(v: number) => [fmt.inr(v), "Cum P&L"]} />
                <Area type="monotone" dataKey="pnl" stroke={isPositive ? "#10b981" : "#ef4444"} fill="url(#btGrad)" strokeWidth={1.5} dot={false} />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        )}

        {/* Trade table */}
        <div className="flex-1 overflow-auto">
          {!running && !trades.length && (
            <div className="flex items-center justify-center h-full text-xs text-content-muted">
              Configure parameters and click Run Backtest
            </div>
          )}
          {trades.length > 0 && (
            <table className="data-table">
              <thead className="sticky top-0 bg-surface-800">
                <tr>
                  {["Symbol", "Date", "P&L ₹", "Exit Reason"].map((h) => (
                    <th key={h}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {trades.map((t, i) => {
                  const up = (t.pnl_inr ?? 0) >= 0;
                  return (
                    <tr key={i}>
                      <td className="font-semibold text-content-primary">{t.symbol}</td>
                      <td className="text-content-muted">{t.entry_date?.slice(0, 10) ?? "—"}</td>
                      <td className={cn("num", up ? "positive" : "negative")}>{fmt.inr(t.pnl_inr)}</td>
                      <td className="text-content-muted">{t.exit_reason ?? "—"}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </div>
  );
}
