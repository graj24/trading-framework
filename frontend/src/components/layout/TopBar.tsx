import { useEffect, useState } from "react";
import { useMarketStore } from "@/store/useMarketStore";
import { useTradeStore } from "@/store/useTradeStore";
import { fmt, colorPnl } from "@/lib/formatters";
import clsx from "clsx";

const REGIME_COLORS: Record<string, string> = {
  trending_bull: "bg-green/20 text-green border-green/40",
  trending_bear: "bg-red/20 text-red border-red/40",
  high_volatility: "bg-orange/20 text-orange border-orange/40",
  ranging: "bg-gold/20 text-gold border-gold/40",
  unknown: "bg-bg-tertiary text-text-secondary border-border",
};

export function TopBar() {
  const { regime, ltpMap } = useMarketStore();
  const { totalPnlInr, totalPnlPct } = useTradeStore();
  const [time, setTime] = useState(fmt.time());

  useEffect(() => {
    const t = setInterval(() => setTime(fmt.time()), 1000);
    return () => clearInterval(t);
  }, []);

  const tickers = Object.entries(ltpMap).slice(0, 20);
  const regimeLabel = regime.replace(/_/g, " ").toUpperCase();

  return (
    <div className="h-9 bg-bg-secondary border-b border-border flex items-center gap-0 shrink-0 overflow-hidden">
      {/* Ticker tape */}
      <div className="flex-1 overflow-hidden relative">
        <div className="ticker-tape flex gap-8 px-4">
          {[...tickers, ...tickers].map(([sym, d], i) => (
            <span key={i} className="mono text-xs flex gap-1.5 items-center">
              <span className="text-text-secondary">{sym}</span>
              <span className={d.change_pct >= 0 ? "text-green" : "text-red"}>
                {fmt.inr(d.price)}
              </span>
              <span className={clsx("text-xs", d.change_pct >= 0 ? "text-green" : "text-red")}>
                {fmt.pct(d.change_pct)}
              </span>
            </span>
          ))}
          {tickers.length === 0 && (
            <span className="text-text-muted text-xs">Connecting to live feed…</span>
          )}
        </div>
      </div>

      {/* Regime badge */}
      <div className={clsx("px-2 py-0.5 rounded border text-xs font-semibold mx-2 shrink-0", REGIME_COLORS[regime] || REGIME_COLORS.unknown)}>
        {regimeLabel || "REGIME: —"}
      </div>

      {/* P&L */}
      <div className={clsx("mono text-xs px-3 shrink-0", colorPnl(totalPnlInr))}>
        P&L {fmt.inr(totalPnlInr)} ({fmt.pct(totalPnlPct)})
      </div>

      {/* Clock */}
      <div className="mono text-xs text-text-secondary px-3 border-l border-border shrink-0">
        IST {time}
      </div>
    </div>
  );
}
