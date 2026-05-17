import { cn } from "@/lib/cn";
import { fmt } from "@/lib/formatters";
import { useMarketStore } from "@/store/useMarketStore";

interface Position {
  symbol: string;
  entry_price?: number;
  stop_loss?: number;
  target?: number;
  position_size?: number;
  entry_date?: string;
  outcome?: string;
}

export function PositionCard({ pos }: { pos: Position }) {
  const ltp = useMarketStore((s) => s.ltpMap[pos.symbol]);
  const entry = pos.entry_price ?? 0;
  const ltpPrice = typeof ltp === "object" && ltp !== null ? (ltp as any).price : (ltp as number | undefined);
  const current = ltpPrice ?? entry;
  const pnl = pos.position_size && entry ? (current - entry) * (pos.position_size / entry) : 0;
  const pnlPct = entry ? ((current - entry) / entry) * 100 : 0;
  const up = pnl >= 0;

  // Progress bar: how far between SL and target
  const sl = pos.stop_loss ?? entry * 0.99;
  const tp = pos.target ?? entry * 1.025;
  const range = tp - sl;
  const progress = range > 0 ? Math.max(0, Math.min(100, ((current - sl) / range) * 100)) : 50;

  return (
    <div className={cn(
      "panel p-3 flex flex-col gap-2 border-l-2 transition-all",
      up ? "border-l-accent-success" : "border-l-accent-danger"
    )}>
      <div className="flex items-center justify-between">
        <span className="text-sm font-bold text-content-primary">{pos.symbol}</span>
        <span className={cn("num text-sm font-bold", up ? "positive" : "negative")}>
          {up ? "+" : ""}{fmt.inr(pnl)}
        </span>
      </div>

      <div className="flex items-center justify-between text-xs text-content-muted">
        <span>Entry <span className="num text-content-secondary">₹{entry.toFixed(2)}</span></span>
        <span>LTP <span className={cn("num font-semibold", up ? "positive" : "negative")}>₹{(current as number).toFixed(2)}</span></span>
        <span className={cn("num", up ? "positive" : "negative")}>{up ? "+" : ""}{pnlPct.toFixed(2)}%</span>
      </div>

      {/* SL → TP progress bar */}
      <div className="flex flex-col gap-1">
        <div className="h-1.5 bg-surface-700 rounded-full overflow-hidden relative">
          <div
            className={cn("h-full rounded-full transition-all duration-500", up ? "bg-accent-success" : "bg-accent-danger")}
            style={{ width: `${progress}%` }}
          />
        </div>
        <div className="flex justify-between text-[10px] text-content-muted num">
          <span>SL ₹{sl.toFixed(0)}</span>
          <span>TP ₹{tp.toFixed(0)}</span>
        </div>
      </div>
    </div>
  );
}
