import { useEffect, useRef } from "react";
import { createChart, ColorType } from "lightweight-charts";
import type { Candle } from "@/lib/api";
import { useMarketStore } from "@/store/useMarketStore";
import { fmt } from "@/lib/formatters";
import { AnimatedNumber } from "@/components/ui/AnimatedNumber";
import { cn } from "@/lib/cn";

interface Props {
  candles: Candle[];
  symbol: string;
}

export function PnLChart({ candles, symbol }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const ltp = useMarketStore((s) => s.ltpMap[symbol]);

  useEffect(() => {
    if (!ref.current || !candles.length) return;

    const chart = createChart(ref.current, {
      layout: {
        background: { type: ColorType.Solid, color: "#0a0e17" },
        textColor: "#6b7280",
        fontSize: 11,
        fontFamily: "'JetBrains Mono', monospace",
      },
      grid: {
        vertLines: { color: "#1f2937", style: 1 },
        horzLines: { color: "#1f2937", style: 1 },
      },
      crosshair: {
        mode: 1,
        vertLine: { color: "#374151", labelBackgroundColor: "#1f2937" },
        horzLine: { color: "#374151", labelBackgroundColor: "#1f2937" },
      },
      rightPriceScale: { borderColor: "#1f2937" },
      timeScale: { borderColor: "#1f2937", timeVisible: true, secondsVisible: false },
      width: ref.current.clientWidth,
      height: ref.current.clientHeight,
    });

    const series = chart.addCandlestickSeries({
      upColor:        "#10b981",
      downColor:      "#ef4444",
      borderUpColor:  "#10b981",
      borderDownColor:"#ef4444",
      wickUpColor:    "#10b981",
      wickDownColor:  "#ef4444",
    });

    series.setData(candles as any);
    chart.timeScale().fitContent();

    const ro = new ResizeObserver(() => {
      if (ref.current) {
        chart.applyOptions({ width: ref.current.clientWidth, height: ref.current.clientHeight });
      }
    });
    ro.observe(ref.current);

    return () => { ro.disconnect(); chart.remove(); };
  }, [candles]);

  const last = candles[candles.length - 1];
  const dayChange = last ? ((last.close - last.open) / last.open) * 100 : null;
  const up = (dayChange ?? 0) >= 0;

  return (
    <div className="flex flex-col h-full bg-surface-900">
      {/* Chart header */}
      <div className="flex items-center gap-4 px-4 py-2 border-b border-surface-700 shrink-0">
        <div>
          <span className="text-sm font-bold text-content-primary">{symbol}</span>
          <span className="text-xs text-content-muted ml-2">NSE</span>
        </div>
        {ltp && (
          <>
            <AnimatedNumber
              value={ltp.price}
              format={fmt.inr}
              className="text-lg font-bold"
            />
            <span className={cn("num text-sm font-medium", ltp.change_pct >= 0 ? "positive" : "negative")}>
              {fmt.pct(ltp.change_pct)}
            </span>
          </>
        )}
        {last && (
          <div className="flex items-center gap-3 ml-auto text-xs text-content-muted">
            <span>O <span className="num text-content-secondary">{fmt.inr(last.open)}</span></span>
            <span>H <span className="num text-accent-success">{fmt.inr(last.high)}</span></span>
            <span>L <span className="num text-accent-danger">{fmt.inr(last.low)}</span></span>
            <span>C <span className="num text-content-secondary">{fmt.inr(last.close)}</span></span>
          </div>
        )}
      </div>
      <div ref={ref} className="flex-1" />
    </div>
  );
}
