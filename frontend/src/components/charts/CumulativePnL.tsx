import { useMemo } from "react";
import { AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer } from "recharts";
import { useTradeStore } from "@/store/useTradeStore";
import { fmt } from "@/lib/formatters";

export function CumulativePnL() {
  const trades = useTradeStore((s) => s.trades);

  const data = useMemo(() => {
    const closed = trades
      .filter((t) => t.exit_date && t.pnl_inr != null)
      .sort((a, b) => new Date(a.exit_date!).getTime() - new Date(b.exit_date!).getTime());

    let cum = 0;
    return closed.map((t) => {
      cum += t.pnl_inr!;
      return { date: t.exit_date!.slice(0, 10), pnl: Math.round(cum * 100) / 100 };
    });
  }, [trades]);

  if (!data.length) {
    return (
      <div className="flex flex-col h-full">
        <div className="px-3 py-1.5 border-b border-border text-xs text-text-secondary font-semibold tracking-wider">CUMULATIVE P&L</div>
        <div className="flex-1 flex items-center justify-center text-text-muted text-xs">No closed trades yet</div>
      </div>
    );
  }

  const isPositive = data[data.length - 1].pnl >= 0;

  return (
    <div className="flex flex-col h-full">
      <div className="px-3 py-1.5 border-b border-border text-xs text-text-secondary font-semibold tracking-wider">
        CUMULATIVE P&L — {fmt.inr(data[data.length - 1].pnl)}
      </div>
      <div className="flex-1 p-2">
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={data}>
            <defs>
              <linearGradient id="pnlGrad" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor={isPositive ? "#00d4aa" : "#ff4d4d"} stopOpacity={0.3} />
                <stop offset="95%" stopColor={isPositive ? "#00d4aa" : "#ff4d4d"} stopOpacity={0} />
              </linearGradient>
            </defs>
            <XAxis dataKey="date" tick={{ fill: "#4b5563", fontSize: 10 }} tickLine={false} axisLine={false} />
            <YAxis tick={{ fill: "#4b5563", fontSize: 10 }} tickLine={false} axisLine={false} tickFormatter={(v) => "₹" + v} />
            <Tooltip
              contentStyle={{ background: "#111827", border: "1px solid #1f2937", borderRadius: 4, fontSize: 11 }}
              formatter={(v: number) => [fmt.inr(v), "P&L"]}
            />
            <Area
              type="monotone"
              dataKey="pnl"
              stroke={isPositive ? "#00d4aa" : "#ff4d4d"}
              fill="url(#pnlGrad)"
              strokeWidth={1.5}
              dot={false}
            />
          </AreaChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
