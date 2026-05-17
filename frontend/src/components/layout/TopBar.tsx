import { useEffect, useState } from "react";
import { TrendingUp, TrendingDown, Minus, Activity, Clock } from "lucide-react";
import { useMarketStore } from "@/store/useMarketStore";
import { useTradeStore } from "@/store/useTradeStore";
import { fmt } from "@/lib/formatters";
import { Badge } from "@/components/ui/Badge";
import { AnimatedNumber } from "@/components/ui/AnimatedNumber";
import { cn } from "@/lib/cn";

const REGIME_CONFIG: Record<string, { label: string; variant: "success" | "danger" | "warning" | "default"; Icon: React.ElementType }> = {
  trending_bull:   { label: "BULL",    variant: "success", Icon: TrendingUp },
  trending_bear:   { label: "BEAR",    variant: "danger",  Icon: TrendingDown },
  high_volatility: { label: "VOLATILE",variant: "warning", Icon: Activity },
  ranging:         { label: "RANGING", variant: "default", Icon: Minus },
  unknown:         { label: "—",       variant: "default", Icon: Minus },
};

function MarketStatus() {
  const now = new Date();
  const h = now.getHours(), m = now.getMinutes();
  const isOpen = h > 9 || (h === 9 && m >= 15);
  const isClose = h >= 15 && m >= 30;
  const open = isOpen && !isClose;
  return (
    <div className="flex items-center gap-1.5">
      <span className={cn("w-1.5 h-1.5 rounded-full", open ? "bg-accent-success animate-pulse" : "bg-content-muted")} />
      <span className={cn("text-xs font-medium", open ? "text-accent-success" : "text-content-muted")}>
        {open ? "MARKET OPEN" : "MARKET CLOSED"}
      </span>
    </div>
  );
}

export function TopBar() {
  const { regime, ltpMap } = useMarketStore();
  const { totalPnlInr, totalPnlPct } = useTradeStore();
  const [time, setTime] = useState(fmt.time());

  useEffect(() => {
    const t = setInterval(() => setTime(fmt.time()), 1000);
    return () => clearInterval(t);
  }, []);

  const regimeCfg = REGIME_CONFIG[regime] ?? REGIME_CONFIG.unknown;
  const tickers = Object.entries(ltpMap).slice(0, 20);
  const pnlPositive = totalPnlInr >= 0;

  return (
    <div className="h-10 bg-surface-950 border-b border-surface-700 flex items-center shrink-0 overflow-hidden">
      {/* Brand */}
      <div className="w-14 flex items-center justify-center shrink-0 border-r border-surface-700 h-full">
        <span className="text-xs font-bold text-gradient-blue tracking-widest">KIRO</span>
      </div>

      {/* Ticker tape */}
      <div className="flex-1 overflow-hidden relative border-r border-surface-700 h-full flex items-center">
        {tickers.length > 0 ? (
          <div className="flex gap-6 px-4 animate-none overflow-x-auto scrollbar-none">
            {tickers.map(([sym, d]) => (
              <span key={sym} className="flex items-center gap-1.5 shrink-0">
                <span className="text-xs text-content-muted font-medium">{sym}</span>
                <span className={cn("num text-xs font-semibold", d.change_pct >= 0 ? "text-accent-success" : "text-accent-danger")}>
                  {fmt.inr(d.price)}
                </span>
                <span className={cn("text-xs", d.change_pct >= 0 ? "text-accent-success" : "text-accent-danger")}>
                  {fmt.pct(d.change_pct)}
                </span>
              </span>
            ))}
          </div>
        ) : (
          <span className="text-xs text-content-muted px-4">Connecting to live feed…</span>
        )}
      </div>

      {/* Right section */}
      <div className="flex items-center gap-3 px-3 shrink-0">
        {/* Market status */}
        <MarketStatus />

        {/* Regime badge */}
        <Badge variant={regimeCfg.variant} dot>
          <regimeCfg.Icon size={10} />
          {regimeCfg.label}
        </Badge>

        {/* P&L pill */}
        <div className={cn(
          "flex items-center gap-1.5 px-2.5 py-1 rounded-full border text-xs font-semibold num",
          pnlPositive
            ? "bg-accent-success/10 border-accent-success/30 text-accent-success"
            : "bg-accent-danger/10 border-accent-danger/30 text-accent-danger"
        )}>
          {pnlPositive ? <TrendingUp size={11} /> : <TrendingDown size={11} />}
          <AnimatedNumber value={totalPnlInr} format={fmt.inr} />
          <span className="opacity-70">({fmt.pct(totalPnlPct)})</span>
        </div>

        {/* Clock */}
        <div className="flex items-center gap-1.5 text-xs text-content-muted border-l border-surface-700 pl-3">
          <Clock size={11} />
          <span className="num">{time} IST</span>
        </div>
      </div>
    </div>
  );
}
