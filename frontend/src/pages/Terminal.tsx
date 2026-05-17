import { useState, useEffect, useCallback } from "react";
import { useQuery } from "@tanstack/react-query";
import { TrendingUp, TrendingDown, Activity, Briefcase, Zap } from "lucide-react";
import { api } from "@/lib/api";
import { useTradeStore } from "@/store/useTradeStore";
import { useMarketStore } from "@/store/useMarketStore";
import { Watchlist } from "@/components/terminal/Watchlist";
import { PnLChart } from "@/components/terminal/PnLChart";
import { SignalPanel } from "@/components/terminal/SignalPanel";
import { PositionsTable } from "@/components/terminal/PositionsTable";
import { TradeLog } from "@/components/terminal/TradeLog";
import { CumulativePnL } from "@/components/charts/CumulativePnL";
import { AnimatedNumber } from "@/components/ui/AnimatedNumber";
import { Badge } from "@/components/ui/Badge";
import { fmt } from "@/lib/formatters";
import { cn } from "@/lib/cn";

function HeroStat({
  label, value, sub, icon: Icon, positive,
}: {
  label: string; value: string; sub?: string;
  icon: React.ElementType; positive?: boolean;
}) {
  return (
    <div className="flex items-center gap-3 px-4 py-2.5 border-r border-surface-700 last:border-r-0">
      <div className={cn(
        "w-8 h-8 rounded-lg flex items-center justify-center shrink-0",
        positive === true  ? "bg-accent-success/15 text-accent-success" :
        positive === false ? "bg-accent-danger/15 text-accent-danger" :
        "bg-accent-primary/15 text-accent-primary"
      )}>
        <Icon size={15} />
      </div>
      <div>
        <div className="text-[10px] text-content-muted uppercase tracking-wider">{label}</div>
        <div className={cn(
          "num text-sm font-semibold",
          positive === true  ? "text-accent-success" :
          positive === false ? "text-accent-danger" :
          "text-content-primary"
        )}>
          {value}
        </div>
        {sub && <div className="text-[10px] text-content-muted">{sub}</div>}
      </div>
    </div>
  );
}

export function Terminal() {
  const [symbol, setSymbol] = useState("RELIANCE");
  const [runLoading, setRunLoading] = useState(false);
  const { trades, totalPnlInr, totalPnlPct, setTrades } = useTradeStore();
  const regime = useMarketStore((s) => s.regime);

  const { data: tradesData } = useQuery({
    queryKey: ["trades"],
    queryFn: () => api.trades({ limit: "500" }),
    refetchInterval: 30000,
  });
  useEffect(() => { if (tradesData) setTrades(tradesData); }, [tradesData, setTrades]);

  const { data: candles = [] } = useQuery({
    queryKey: ["candles", symbol],
    queryFn: () => api.candles(symbol, "1d"),
    staleTime: 60000,
  });

  const { data: signal, refetch: refetchSignal } = useQuery({
    queryKey: ["signal", symbol],
    queryFn: () => api.signal(symbol),
    staleTime: 30000,
  });

  const handleRun = useCallback(async () => {
    setRunLoading(true);
    await api.runSignal(symbol);
    setTimeout(() => { refetchSignal(); setRunLoading(false); }, 8000);
  }, [symbol, refetchSignal]);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === "g" && !e.ctrlKey && !e.metaKey && !(e.target instanceof HTMLInputElement)) {
        const s = prompt("Go to symbol:");
        if (s) setSymbol(s.toUpperCase().trim());
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, []);

  const openPositions = trades.filter((t) => !t.exit_date);
  const closedTrades  = trades.filter((t) => t.exit_date);
  const wins = closedTrades.filter((t) => (t.pnl_inr ?? 0) > 0);
  const winRate = closedTrades.length > 0 ? (wins.length / closedTrades.length) * 100 : 0;
  const pnlPositive = totalPnlInr >= 0;

  return (
    <div className="flex flex-col h-full overflow-hidden bg-surface-900">
      {/* Hero stats strip */}
      <div className="flex items-stretch border-b border-surface-700 bg-surface-950 shrink-0">
        <HeroStat
          label="Today's P&L"
          value={fmt.inr(totalPnlInr)}
          sub={fmt.pct(totalPnlPct)}
          icon={pnlPositive ? TrendingUp : TrendingDown}
          positive={pnlPositive}
        />
        <HeroStat
          label="Open Positions"
          value={String(openPositions.length)}
          sub="of 3 max"
          icon={Briefcase}
        />
        <HeroStat
          label="Win Rate"
          value={winRate > 0 ? winRate.toFixed(0) + "%" : "—"}
          sub={`${wins.length}/${closedTrades.length} trades`}
          icon={Activity}
          positive={winRate >= 50 ? true : winRate > 0 ? false : undefined}
        />
        <HeroStat
          label="Signal"
          value={signal?.decision ?? "—"}
          sub={signal?.confidence != null ? `${signal.confidence.toFixed(0)}% conf` : undefined}
          icon={Zap}
          positive={signal?.decision === "BUY" ? true : signal?.decision === "SELL" ? false : undefined}
        />
        <div className="flex-1" />
        <div className="flex items-center gap-2 px-4">
          <span className="text-xs text-content-muted">Press</span>
          <kbd className="px-1.5 py-0.5 rounded bg-surface-700 text-content-secondary text-xs font-mono">G</kbd>
          <span className="text-xs text-content-muted">to jump to symbol</span>
        </div>
      </div>

      {/* Main 3-column area */}
      <div className="flex flex-1 overflow-hidden" style={{ minHeight: 0 }}>
        {/* Left: Watchlist */}
        <div className="w-48 border-r border-surface-700 shrink-0 overflow-hidden">
          <Watchlist selected={symbol} onSelect={setSymbol} />
        </div>

        {/* Center: Chart */}
        <div className="flex-1 border-r border-surface-700 overflow-hidden">
          <PnLChart candles={candles} symbol={symbol} />
        </div>

        {/* Right: Signal panel */}
        <div className="w-60 shrink-0 overflow-hidden">
          <SignalPanel signal={signal} loading={runLoading} onRun={handleRun} />
        </div>
      </div>

      {/* Bottom row */}
      <div className="h-44 border-t border-surface-700 flex shrink-0 overflow-hidden">
        <div className="flex-1 border-r border-surface-700 overflow-hidden">
          <PositionsTable />
        </div>
        <div className="w-64 border-r border-surface-700 overflow-hidden">
          <CumulativePnL />
        </div>
        <div className="w-64 overflow-hidden">
          <TradeLog />
        </div>
      </div>
    </div>
  );
}
