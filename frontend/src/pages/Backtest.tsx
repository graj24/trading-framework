import { useState } from "react";
import { streamBacktest } from "@/lib/api";
import { fmt, colorPnl } from "@/lib/formatters";
import { AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer } from "recharts";
import clsx from "clsx";

interface Summary { trades: number; win_rate: number; net_pnl: number }
interface TradeRow { symbol: string; pnl_inr?: number; exit_reason?: string; entry_date?: string }

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
    setRunning(true);
    setTrades([]);
    setSummary(null);
    setEquityData([]);
    setProgress(0);

    const endpoint = strategy === "gap" ? "/api/backtest/gap" : "/api/backtest/intraday";
    const body = { gap_threshold: gapThreshold, sl_pct: slPct, target_pct: targetPct };

    let cum = 0;
    let idx = 0;
    try {
      for await (const event of streamBacktest(endpoint, body)) {
        if (event.type === "progress") setProgress(event.pct as number);
        else if (event.type === "trade") {
          const t = event as unknown as TradeRow;
          setTrades((prev) => [...prev, t]);
          cum += t.pnl_inr ?? 0;
          idx++;
          setEquityData((prev) => [...prev, { i: idx, pnl: Math.round(cum * 100) / 100 }]);
        } else if (event.type === "summary") {
          setSummary(event as unknown as Summary);
          setProgress(100);
        }
      }
    } finally {
      setRunning(false);
    }
  };

  return (
    <div className="flex h-full overflow-hidden">
      {/* Controls */}
      <div className="w-56 border-r border-border bg-bg-secondary flex flex-col gap-4 p-4 shrink-0 overflow-auto">
        <div className="text-xs text-text-secondary font-semibold tracking-wider">STRATEGY</div>
        {(["gap", "intraday"] as const).map((s) => (
          <button
            key={s}
            onClick={() => setStrategy(s)}
            className={clsx("text-xs px-3 py-1.5 rounded border", strategy === s ? "bg-blue/20 border-blue/40 text-blue" : "border-border text-text-muted hover:border-blue/30")}
          >
            {s === "gap" ? "Gap Strategy" : "Intraday ML"}
          </button>
        ))}

        <div className="border-t border-border pt-3 flex flex-col gap-3">
          {[
            { label: "Gap Threshold %", val: gapThreshold, set: setGapThreshold, min: 0.5, max: 10, step: 0.5 },
            { label: "Stop Loss %", val: slPct, set: setSlPct, min: 0.5, max: 5, step: 0.5 },
            { label: "Target %", val: targetPct, set: setTargetPct, min: 1, max: 10, step: 0.5 },
          ].map(({ label, val, set, min, max, step }) => (
            <div key={label} className="flex flex-col gap-1">
              <div className="flex justify-between text-xs">
                <span className="text-text-muted">{label}</span>
                <span className="mono text-text-secondary">{val}%</span>
              </div>
              <input type="range" min={min} max={max} step={step} value={val}
                onChange={(e) => set(parseFloat(e.target.value))}
                className="w-full accent-blue" />
            </div>
          ))}
        </div>

        <button
          onClick={run}
          disabled={running}
          className="mt-auto bg-blue text-bg-primary font-semibold text-xs py-2 rounded hover:bg-blue/80 disabled:opacity-40"
        >
          {running ? `Running… ${progress}%` : "▶ Run Backtest"}
        </button>
      </div>

      {/* Results */}
      <div className="flex-1 flex flex-col overflow-hidden">
        {/* Summary metrics */}
        {summary && (
          <div className="flex gap-4 p-3 border-b border-border shrink-0">
            {[
              { label: "TRADES", val: summary.trades.toString() },
              { label: "WIN RATE", val: summary.win_rate.toFixed(1) + "%" },
              { label: "NET P&L", val: fmt.inr(summary.net_pnl), color: colorPnl(summary.net_pnl) },
            ].map(({ label, val, color }) => (
              <div key={label} className="metric-card flex-1">
                <div className="text-text-muted text-[10px] tracking-wider">{label}</div>
                <div className={clsx("mono text-lg font-bold mt-0.5", color ?? "text-text-primary")}>{val}</div>
              </div>
            ))}
          </div>
        )}

        {/* Equity curve */}
        {equityData.length > 0 && (
          <div className="h-40 border-b border-border shrink-0 p-2">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={equityData}>
                <defs>
                  <linearGradient id="btGrad" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#00d4aa" stopOpacity={0.3} />
                    <stop offset="95%" stopColor="#00d4aa" stopOpacity={0} />
                  </linearGradient>
                </defs>
                <XAxis dataKey="i" hide />
                <YAxis tick={{ fill: "#4b5563", fontSize: 10 }} tickLine={false} axisLine={false} tickFormatter={(v) => "₹" + v} />
                <Tooltip contentStyle={{ background: "#111827", border: "1px solid #1f2937", fontSize: 11 }} formatter={(v: number) => [fmt.inr(v), "Cum P&L"]} />
                <Area type="monotone" dataKey="pnl" stroke="#00d4aa" fill="url(#btGrad)" strokeWidth={1.5} dot={false} />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        )}

        {/* Trade table */}
        <div className="flex-1 overflow-auto">
          {!running && !trades.length && (
            <div className="flex items-center justify-center h-full text-text-muted text-xs">
              Configure parameters and click Run Backtest
            </div>
          )}
          {trades.length > 0 && (
            <table className="w-full text-xs">
              <thead className="sticky top-0 bg-bg-secondary">
                <tr className="text-text-muted border-b border-border">
                  {["SYMBOL", "DATE", "P&L₹", "EXIT REASON"].map((h) => (
                    <th key={h} className="px-3 py-1.5 text-left font-normal">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {trades.map((t, i) => (
                  <tr key={i} className="border-b border-border/30 hover:bg-bg-tertiary">
                    <td className="px-3 py-1 font-semibold">{t.symbol}</td>
                    <td className="px-3 py-1 text-text-muted">{t.entry_date?.slice(0, 10) ?? "—"}</td>
                    <td className={clsx("px-3 py-1 mono", colorPnl(t.pnl_inr))}>{fmt.inr(t.pnl_inr)}</td>
                    <td className="px-3 py-1 text-text-muted">{t.exit_reason ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </div>
  );
}
