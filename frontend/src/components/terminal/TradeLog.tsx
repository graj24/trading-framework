import { useTradeStore } from "@/store/useTradeStore";
import { fmt, colorPnl } from "@/lib/formatters";
import clsx from "clsx";

export function TradeLog() {
  const trades = useTradeStore((s) => s.trades);
  const closed = trades.filter((t) => t.exit_date).slice(0, 50);

  return (
    <div className="flex flex-col h-full">
      <div className="px-3 py-1.5 border-b border-border text-xs text-text-secondary font-semibold tracking-wider">
        TRADE LOG ({closed.length})
      </div>
      <div className="overflow-auto flex-1">
        {closed.length === 0 && (
          <div className="text-center text-text-muted text-xs py-6">No closed trades</div>
        )}
        {closed.map((t) => (
          <div key={t.id} className="flex items-center gap-2 px-3 py-1.5 border-b border-border/30 hover:bg-bg-tertiary text-xs">
            <span className="font-semibold text-text-primary w-20 shrink-0">{t.symbol}</span>
            <span className="text-text-muted flex-1">{fmt.date(t.exit_date)}</span>
            <span className={clsx("mono", colorPnl(t.pnl_inr))}>{fmt.inr(t.pnl_inr)}</span>
            <span className={clsx("mono text-xs", colorPnl(t.pnl_pct))}>{fmt.pct(t.pnl_pct)}</span>
            <span className={clsx("text-xs px-1 rounded", t.outcome === "WIN" ? "text-green" : t.outcome === "LOSS" ? "text-red" : "text-text-muted")}>
              {t.outcome ?? "—"}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
