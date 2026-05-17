import { useMarketStore } from "@/store/useMarketStore";
import { fmt } from "@/lib/formatters";
import clsx from "clsx";

const DEFAULT_SYMBOLS = [
  "RELIANCE","TCS","HDFCBANK","INFY","ICICIBANK",
  "HINDUNILVR","SBIN","BHARTIARTL","ITC","KOTAKBANK",
  "LT","AXISBANK","ASIANPAINT","MARUTI","TITAN",
];

interface Props {
  selected: string;
  onSelect: (s: string) => void;
}

export function Watchlist({ selected, onSelect }: Props) {
  const ltpMap = useMarketStore((s) => s.ltpMap);

  return (
    <div className="flex flex-col h-full">
      <div className="px-3 py-1.5 border-b border-border text-xs text-text-secondary font-semibold tracking-wider">
        WATCHLIST
      </div>
      <div className="overflow-auto flex-1">
        {DEFAULT_SYMBOLS.map((sym) => {
          const d = ltpMap[sym];
          const isSelected = sym === selected;
          return (
            <button
              key={sym}
              onClick={() => onSelect(sym)}
              className={clsx(
                "w-full flex items-center justify-between px-3 py-1.5 text-xs border-b border-border/30 hover:bg-bg-tertiary transition-colors",
                isSelected && "bg-blue/10 border-l-2 border-l-blue"
              )}
            >
              <span className={clsx("font-semibold", isSelected ? "text-blue" : "text-text-primary")}>
                {sym}
              </span>
              {d ? (
                <div className="flex flex-col items-end">
                  <span className="mono">{fmt.inr(d.price)}</span>
                  <span className={clsx("mono text-[10px]", d.change_pct >= 0 ? "text-green" : "text-red")}>
                    {fmt.pct(d.change_pct)}
                  </span>
                </div>
              ) : (
                <span className="text-text-muted">—</span>
              )}
            </button>
          );
        })}
      </div>
    </div>
  );
}
