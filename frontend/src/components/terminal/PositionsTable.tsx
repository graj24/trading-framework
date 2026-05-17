import { useEffect, useRef } from "react";
import { useTradeStore } from "@/store/useTradeStore";
import { useMarketStore } from "@/store/useMarketStore";
import { fmt, colorPnl } from "@/lib/formatters";
import clsx from "clsx";

export function PositionsTable() {
  const trades = useTradeStore((s) => s.trades);
  const ltpMap = useMarketStore((s) => s.ltpMap);
  const open = trades.filter((t) => !t.exit_date);

  return (
    <div className="flex flex-col h-full">
      <div className="px-3 py-1.5 border-b border-border text-xs text-text-secondary font-semibold tracking-wider">
        OPEN POSITIONS ({open.length})
      </div>
      <div className="overflow-auto flex-1">
        <table className="w-full text-xs">
          <thead>
            <tr className="text-text-muted border-b border-border">
              {["SYMBOL", "ENTRY", "LTP", "P&L%", "P&L₹", "SL", "TARGET", "AGE"].map((h) => (
                <th key={h} className="px-2 py-1 text-right first:text-left font-normal">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {open.length === 0 && (
              <tr>
                <td colSpan={8} className="text-center text-text-muted py-6">No open positions</td>
              </tr>
            )}
            {open.map((t) => {
              const ltp = ltpMap[t.symbol]?.price ?? t.entry_price ?? 0;
              const entry = t.entry_price ?? 0;
              const livePnlPct = entry ? ((ltp - entry) / entry) * 100 : t.pnl_pct ?? 0;
              const livePnlInr = entry && t.position_size ? (ltp - entry) * t.position_size : t.pnl_inr ?? 0;
              const age = t.entry_date
                ? Math.floor((Date.now() - new Date(t.entry_date).getTime()) / 86400000) + "d"
                : "—";
              return (
                <tr key={t.id} className="border-b border-border/50 hover:bg-bg-tertiary transition-colors">
                  <td className="px-2 py-1 font-semibold text-text-primary">{t.symbol}</td>
                  <td className="px-2 py-1 mono text-right">{fmt.inr(t.entry_price)}</td>
                  <td className="px-2 py-1 mono text-right">{fmt.inr(ltp)}</td>
                  <td className={clsx("px-2 py-1 mono text-right", colorPnl(livePnlPct))}>{fmt.pct(livePnlPct)}</td>
                  <td className={clsx("px-2 py-1 mono text-right", colorPnl(livePnlInr))}>{fmt.inr(livePnlInr)}</td>
                  <td className="px-2 py-1 mono text-right text-red">{fmt.inr(t.stop_loss)}</td>
                  <td className="px-2 py-1 mono text-right text-green">{fmt.inr(t.target)}</td>
                  <td className="px-2 py-1 mono text-right text-text-muted">{age}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
