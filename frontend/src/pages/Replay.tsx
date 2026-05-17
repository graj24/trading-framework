import { useState } from "react";
import { streamBacktest } from "@/lib/api";
import { fmt, colorPnl } from "@/lib/formatters";
import { AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer } from "recharts";
import clsx from "clsx";

interface Summary { trades: number; win_rate: number; net_pnl: number }

export function Replay() {
  const [startDate, setStartDate] = useState("2024-01-01");
  const [endDate, setEndDate] = useState("2024-12-31");
  const [gapThreshold, setGapThreshold] = useState(2.0);
  const [running, setRunning] = useState(false);
  const [progress, setProgress] = useState(0);
  const [currentDay, setCurrentDay] = useState("");
  const [trades, setTrades] = useState<Record<string, unknown>[]>([]);
  const [summary, setSummary] = useState<Summary | null>(null);
  const [equityData, setEquityData] = useState<{ i: number; pnl: number }[]>([]);

  const run = async () => {
    setRunning(true);
    setTrades([]);
    setSummary(null);
    setEquityData([]);
    setProgress(0);

    let cum = 0;
    let idx = 0;
    try {
      for await (const event of streamBacktest("/api/backtest/replay", {
        start_date: startDate,
        end_date: endDate,
        gap_threshold: gapThreshold,
      })) {
        if (event.type === "progress") {
          setProgress(event.pct as number);
          if (event.date) setCurrentDay(event.date as string);
        } else if (event.type === "trade") {
          setTrades((prev) => [...prev, event]);
          cum += (event.pnl_inr as number) ?? 0;
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
      <div className="w-56 border-r border-border bg-bg-secondary flex flex-col gap-4 p-4 shrink-0">
        <div className="text-xs text-text-secondary font-semibold tracking-wider">DATE RANGE REPLAY</div>

        <div className="flex flex-col gap-1">
          <label className="text-xs text-text-muted">Start Date</label>
          <input type="date" value={startDate} onChange={(e) => setStartDate(e.target.value)}
            className="bg-bg-tertiary border border-border rounded px-2 py-1 text-xs text-text-primary mono focus:border-blue outline-none" />
        </div>
        <div className="flex flex-col gap-1">
          <label className="text-xs text-text-muted">End Date</label>
          <input type="date" value={endDate} onChange={(e) => setEndDate(e.target.value)}
            className="bg-bg-tertiary border border-border rounded px-2 py-1 text-xs text-text-primary mono focus:border-blue outline-none" />
        </div>

        <div className="flex flex-col gap-1">
          <div className="flex justify-between text-xs">
            <span className="text-text-muted">Gap Threshold</span>
            <span className="mono text-text-secondary">{gapThreshold}%</span>
          </div>
          <input type="range" min={0.5} max={10} step={0.5} value={gapThreshold}
            onChange={(e) => setGapThreshold(parseFloat(e.target.value))}
            className="w-full accent-blue" />
        </div>

        <button
          onClick={run}
          disabled={running}
          className="mt-auto bg-blue text-bg-primary font-semibold text-xs py-2 rounded hover:bg-blue/80 disabled:opacity-40"
        >
          {running ? "Replaying…" : "↺ Run Replay"}
        </button>
      </div>

      {/* Results */}
      <div className="flex-1 flex flex-col overflow-hidden">
        {/* Progress bar */}
        {running && (
          <div className="px-4 py-2 border-b border-border shrink-0">
            <div className="flex justify-between text-xs text-text-muted mb-1">
              <span>Processing {currentDay || "…"}</span>
              <span>{progress}%</span>
            </div>
            <div className="h-1 bg-bg-tertiary rounded-full overflow-hidden">
              <div className="h-full bg-blue rounded-full transition-all duration-300" style={{ width: `${progress}%` }} />
            </div>
          </div>
        )}

        {/* Summary */}
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
                  <linearGradient id="rpGrad" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#00d4aa" stopOpacity={0.3} />
                    <stop offset="95%" stopColor="#00d4aa" stopOpacity={0} />
                  </linearGradient>
                </defs>
                <XAxis dataKey="i" hide />
                <YAxis tick={{ fill: "#4b5563", fontSize: 10 }} tickLine={false} axisLine={false} tickFormatter={(v) => "₹" + v} />
                <Tooltip contentStyle={{ background: "#111827", border: "1px solid #1f2937", fontSize: 11 }} formatter={(v: number) => [fmt.inr(v), "Cum P&L"]} />
                <Area type="monotone" dataKey="pnl" stroke="#00d4aa" fill="url(#rpGrad)" strokeWidth={1.5} dot={false} />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        )}

        {/* Trade table */}
        <div className="flex-1 overflow-auto">
          {!running && !trades.length && (
            <div className="flex items-center justify-center h-full text-text-muted text-xs">
              Select a date range and click Run Replay
            </div>
          )}
          {trades.length > 0 && (
            <table className="w-full text-xs">
              <thead className="sticky top-0 bg-bg-secondary">
                <tr className="text-text-muted border-b border-border">
                  {["SYMBOL", "DATE", "P&L₹", "EXIT"].map((h) => (
                    <th key={h} className="px-3 py-1.5 text-left font-normal">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {trades.map((t, i) => (
                  <tr key={i} className="border-b border-border/30 hover:bg-bg-tertiary">
                    <td className="px-3 py-1 font-semibold">{t.symbol as string}</td>
                    <td className="px-3 py-1 text-text-muted">{(t.entry_date as string)?.slice(0, 10) ?? "—"}</td>
                    <td className={clsx("px-3 py-1 mono", colorPnl(t.pnl_inr as number))}>{fmt.inr(t.pnl_inr as number)}</td>
                    <td className="px-3 py-1 text-text-muted">{(t.exit_reason as string) ?? "—"}</td>
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
