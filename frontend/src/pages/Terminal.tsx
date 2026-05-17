import { useState, useEffect, useCallback } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { useTradeStore } from "@/store/useTradeStore";
import { Watchlist } from "@/components/terminal/Watchlist";
import { PnLChart } from "@/components/terminal/PnLChart";
import { SignalPanel } from "@/components/terminal/SignalPanel";
import { PositionsTable } from "@/components/terminal/PositionsTable";
import { TradeLog } from "@/components/terminal/TradeLog";
import { CumulativePnL } from "@/components/charts/CumulativePnL";

export function Terminal() {
  const [symbol, setSymbol] = useState("RELIANCE");
  const [runLoading, setRunLoading] = useState(false);
  const setTrades = useTradeStore((s) => s.setTrades);

  // Load trades
  const { data: trades } = useQuery({
    queryKey: ["trades"],
    queryFn: () => api.trades({ limit: "500" }),
    refetchInterval: 30000,
  });
  useEffect(() => { if (trades) setTrades(trades); }, [trades]);

  // Load candles for selected symbol
  const { data: candles = [] } = useQuery({
    queryKey: ["candles", symbol],
    queryFn: () => api.candles(symbol, "1d"),
    staleTime: 60000,
  });

  // Load signal for selected symbol
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

  // Keyboard shortcut: G = symbol search (simple prompt for now)
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

  return (
    <div className="flex flex-col h-full overflow-hidden">
      {/* 3-column main area */}
      <div className="flex flex-1 overflow-hidden" style={{ minHeight: 0 }}>
        {/* Left: Watchlist */}
        <div className="w-44 border-r border-border shrink-0 overflow-hidden">
          <Watchlist selected={symbol} onSelect={setSymbol} />
        </div>

        {/* Center: Chart */}
        <div className="flex-1 border-r border-border overflow-hidden">
          <PnLChart candles={candles} symbol={symbol} />
        </div>

        {/* Right: Signal panel */}
        <div className="w-56 shrink-0 overflow-hidden">
          <SignalPanel signal={signal} loading={runLoading} onRun={handleRun} />
        </div>
      </div>

      {/* Bottom row */}
      <div className="h-48 border-t border-border flex shrink-0 overflow-hidden">
        {/* Positions table */}
        <div className="flex-1 border-r border-border overflow-hidden">
          <PositionsTable />
        </div>
        {/* Cumulative P&L */}
        <div className="w-72 border-r border-border overflow-hidden">
          <CumulativePnL />
        </div>
        {/* Trade log */}
        <div className="w-72 overflow-hidden">
          <TradeLog />
        </div>
      </div>
    </div>
  );
}
