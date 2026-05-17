import { useMarketStore } from "@/store/useMarketStore";
import { fmt } from "@/lib/formatters";
import { Sparkline } from "@/components/ui/Sparkline";
import { AnimatedNumber } from "@/components/ui/AnimatedNumber";
import { cn } from "@/lib/cn";
import { useRef } from "react";

const DEFAULT_SYMBOLS = [
  "RELIANCE","TCS","HDFCBANK","INFY","ICICIBANK",
  "HINDUNILVR","SBIN","BHARTIARTL","ITC","KOTAKBANK",
  "LT","AXISBANK","ASIANPAINT","MARUTI","TITAN",
  "SUNPHARMA","ULTRACEMCO","BAJFINANCE","WIPRO","HCLTECH",
];

// Fake sparkline history until real data arrives
function usePriceHistory(symbol: string, currentPrice?: number) {
  const histRef = useRef<Record<string, number[]>>({});
  if (currentPrice) {
    if (!histRef.current[symbol]) histRef.current[symbol] = [];
    const hist = histRef.current[symbol];
    if (hist.length === 0 || hist[hist.length - 1] !== currentPrice) {
      hist.push(currentPrice);
      if (hist.length > 20) hist.shift();
    }
  }
  return histRef.current[symbol] ?? [];
}

interface Props {
  selected: string;
  onSelect: (s: string) => void;
}

function WatchlistRow({ sym, selected, onSelect }: { sym: string; selected: boolean; onSelect: () => void }) {
  const d = useMarketStore((s) => s.ltpMap[sym]);
  const history = usePriceHistory(sym, d?.price);
  const up = (d?.change_pct ?? 0) >= 0;

  return (
    <button
      onClick={onSelect}
      className={cn(
        "w-full flex items-center gap-2 px-3 py-2 text-xs border-b border-surface-700/40 hover:bg-surface-750 transition-colors group",
        selected && "bg-accent-primary/10 border-l-2 border-l-accent-primary"
      )}
    >
      {/* Symbol */}
      <div className="flex-1 min-w-0 text-left">
        <div className={cn("font-semibold truncate", selected ? "text-accent-primary" : "text-content-primary")}>
          {sym}
        </div>
        {d && (
          <div className={cn("num text-[10px]", up ? "text-accent-success" : "text-accent-danger")}>
            {fmt.pct(d.change_pct)}
          </div>
        )}
      </div>

      {/* Sparkline */}
      <Sparkline data={history} width={44} height={20} />

      {/* Price */}
      {d ? (
        <div className="text-right shrink-0">
          <AnimatedNumber value={d.price} format={fmt.inr} className="text-xs font-medium" />
        </div>
      ) : (
        <span className="text-content-muted text-xs">—</span>
      )}
    </button>
  );
}

export function Watchlist({ selected, onSelect }: Props) {
  return (
    <div className="flex flex-col h-full bg-surface-900">
      <div className="panel-header border-b border-surface-700">
        <span className="panel-title">Watchlist</span>
        <span className="text-xs text-content-muted">{DEFAULT_SYMBOLS.length}</span>
      </div>
      <div className="overflow-y-auto flex-1">
        {DEFAULT_SYMBOLS.map((sym) => (
          <WatchlistRow key={sym} sym={sym} selected={sym === selected} onSelect={() => onSelect(sym)} />
        ))}
      </div>
    </div>
  );
}
