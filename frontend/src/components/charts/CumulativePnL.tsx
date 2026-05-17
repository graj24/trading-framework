import { useMemo } from "react";
import { AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer, ReferenceLine } from "recharts";
import { useTradeStore } from "@/store/useTradeStore";
import { fmt } from "@/lib/formatters";
import { cn } from "@/lib/cn";

export function CumulativePnL() {
  const trades = useTradeStore((s) => s.trades);

  const { data, final } = useMemo(() => {
    const closed = trades
      .filter((t) => t.exit_date && t.pnl_inr != null)
      .sort((a, b) => new Date(a.exit_date!).getTime() - new Date(b.exit_date!).getTime());
    let cum = 0;
    const data = closed.map((t) => {
      cum += t.pnl_inr!;
      return { date: t.exit_date!.slice(5, 10), pnl: Math.round(cum * 100) / 100 };
    });
    return { data, final: cum };
  }, [trades]);

  const isPositive = final >= 0;
  const color = isPositive ? "#10b981" : "#ef4444";

  return (
    <div className="flex flex-col h-full">
      <div className="panel-header border-b border-surface-700">
        <span className="panel-title">Cumulative P&L</span>
        <span className={cn("num text-xs font-semibold", isPositive ? "positive" : "negative")}>
          {fmt.inr(final)}
        </span>
      </div>
      {data.length === 0 ? (
        <div className="flex-1 flex items-center justify-center text-xs text-content-muted">
          No closed trades yet
        </div>
      ) : (
        <div className="flex-1 p-2">
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={data} margin={{ top: 4, right: 4, bottom: 0, left: 0 }}>
              <defs>
                <linearGradient id="pnlGrad" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor={color} stopOpacity={0.25} />
                  <stop offset="95%" stopColor={color} stopOpacity={0} />
                </linearGradient>
              </defs>
              <XAxis dataKey="date" tick={{ fill: "#6b7280", fontSize: 9 }} tickLine={false} axisLine={false} />
              <YAxis tick={{ fill: "#6b7280", fontSize: 9 }} tickLine={false} axisLine={false} tickFormatter={(v) => "₹" + v} width={40} />
              <ReferenceLine y={0} stroke="#374151" strokeDasharray="3 3" />
              <Tooltip
                contentStyle={{ background: "#111827", border: "1px solid #1f2937", borderRadius: 8, fontSize: 11 }}
                formatter={(v: number) => [fmt.inr(v), "P&L"]}
              />
              <Area type="monotone" dataKey="pnl" stroke={color} fill="url(#pnlGrad)" strokeWidth={1.5} dot={false} />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      )}
    </div>
  );
}
