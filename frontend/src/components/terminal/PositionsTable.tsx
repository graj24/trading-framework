import { useTradeStore } from "@/store/useTradeStore";
import { useMarketStore } from "@/store/useMarketStore";
import { fmt } from "@/lib/formatters";
import { Badge } from "@/components/ui/Badge";
import { cn } from "@/lib/cn";

export function PositionsTable() {
  const trades = useTradeStore((s) => s.trades);
  const ltpMap = useMarketStore((s) => s.ltpMap);
  const open = trades.filter((t) => !t.exit_date);

  return (
    <div className="flex flex-col h-full">
      <div className="panel-header border-b border-surface-700">
        <span className="panel-title">Open Positions</span>
        <Badge variant={open.length > 0 ? "info" : "default"}>{open.length}</Badge>
      </div>
      <div className="overflow-auto flex-1">
        {open.length === 0 ? (
          <div className="flex items-center justify-center h-full text-xs text-content-muted">
            No open positions
          </div>
        ) : (
          <table className="data-table">
            <thead>
              <tr>
                {["Symbol", "Entry", "LTP", "P&L%", "P&L ₹", "SL", "Target", "Age"].map((h) => (
                  <th key={h} className={h === "Symbol" ? "text-left" : "text-right"}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {open.map((t) => {
                const ltp = ltpMap[t.symbol]?.price ?? t.entry_price ?? 0;
                const entry = t.entry_price ?? 0;
                const livePnlPct = entry ? ((ltp - entry) / entry) * 100 : (t.pnl_pct ?? 0);
                const livePnlInr = entry && t.position_size ? (ltp - entry) * t.position_size : (t.pnl_inr ?? 0);
                const age = t.entry_date
                  ? Math.floor((Date.now() - new Date(t.entry_date).getTime()) / 86400000) + "d"
                  : "—";
                const up = livePnlInr >= 0;
                return (
                  <tr key={t.id}>
                    <td className="font-semibold text-content-primary">{t.symbol}</td>
                    <td className="num text-right">{fmt.inr(t.entry_price)}</td>
                    <td className="num text-right text-content-primary">{fmt.inr(ltp)}</td>
                    <td className={cn("num text-right", up ? "positive" : "negative")}>{fmt.pct(livePnlPct)}</td>
                    <td className={cn("num text-right font-medium", up ? "positive" : "negative")}>{fmt.inr(livePnlInr)}</td>
                    <td className="num text-right text-accent-danger">{fmt.inr(t.stop_loss)}</td>
                    <td className="num text-right text-accent-success">{fmt.inr(t.target)}</td>
                    <td className="num text-right text-content-muted">{age}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
