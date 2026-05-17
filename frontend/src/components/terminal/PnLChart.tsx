import { useEffect, useRef } from "react";
import { createChart, ColorType, LineStyle } from "lightweight-charts";
import type { Candle } from "@/lib/api";

interface Props {
  candles: Candle[];
  symbol: string;
}

export function PnLChart({ candles, symbol }: Props) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!ref.current || !candles.length) return;

    const chart = createChart(ref.current, {
      layout: {
        background: { type: ColorType.Solid, color: "#111827" },
        textColor: "#9ca3af",
      },
      grid: {
        vertLines: { color: "#1f2937" },
        horzLines: { color: "#1f2937" },
      },
      crosshair: { mode: 1 },
      rightPriceScale: { borderColor: "#1f2937" },
      timeScale: { borderColor: "#1f2937", timeVisible: true },
      width: ref.current.clientWidth,
      height: ref.current.clientHeight,
    });

    const series = chart.addCandlestickSeries({
      upColor: "#00d4aa",
      downColor: "#ff4d4d",
      borderUpColor: "#00d4aa",
      borderDownColor: "#ff4d4d",
      wickUpColor: "#00d4aa",
      wickDownColor: "#ff4d4d",
    });

    series.setData(candles as any);
    chart.timeScale().fitContent();

    const ro = new ResizeObserver(() => {
      if (ref.current) {
        chart.applyOptions({ width: ref.current.clientWidth, height: ref.current.clientHeight });
      }
    });
    ro.observe(ref.current);

    return () => {
      ro.disconnect();
      chart.remove();
    };
  }, [candles]);

  return (
    <div className="flex flex-col h-full">
      <div className="px-3 py-1.5 border-b border-border text-xs text-text-secondary font-semibold tracking-wider">
        {symbol} — PRICE CHART
      </div>
      <div ref={ref} className="flex-1" />
    </div>
  );
}
