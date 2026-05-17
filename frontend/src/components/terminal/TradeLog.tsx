import { useTradeStore } from "@/store/useTradeStore";
import { fmt } from "@/lib/formatters";
import { Badge } from "@/components/ui/Badge";
import { cn } from "@/lib/cn";

export function TradeLog() {
  const trades = useTradeStore((s) => s.trades);
  const closed = trades.filter((t) => t.exit_date).slice(0, 50);

  return (
    <div className="flex flex-col h-full">
      <div className="panel-header border-b border-surface-700">
        <span className="panel-title">Trade Log</span>
        <Badge variant="default">{closed.length}</Badge>
      </div>
      <div className="overflow-y-auto flex-1">
        {closed.length === 0 ? (
          <div className="flex items-center justify-center h-full text-xs text-content-muted">
            No closed trades
          </div>
        ) : (
          closed.map((t) => {
            const win = t.outcome === "WIN" || (t.pnl_inr ?? 0) > 0;
            const loss = t.outcome === "LOSS" || (t.pnl_inr ?? 0) < 0;
            return (
              <div key={t.id} className="flex items-center gap-2 px-3 py-2 border-b border-surface-700/40 hover:bg-surface-750 transition-colors">
                <div className={cn("w-1 h-6 rounded-full shrink-0", win ? "bg-accent-success" : loss ? "bg-accent-danger" : "bg-surface-600")} />
                <div className="flex-1 min-w-0">
                  <div className="font-semibold text-xs text-content-primary">{t.symbol}</div>
                  <div className="text-[10px] text-content-muted">{fmt.date(t.exit_date)}</div>
                </div>
                <div className="text-right">
                  <div className={cn("num text-xs font-medium", win ? "positive" : loss ? "negative" : "neutral")}>
                    {fmt.inr(t.pnl_inr)}
                  </div>
                  <div className={cn("num text-[10px]", win ? "positive" : loss ? "negative" : "neutral")}>
                    {fmt.pct(t.pnl_pct)}
                  </div>
                </div>
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}
