import { useMemo } from "react";
import { cn } from "@/lib/cn";

interface SparklineProps {
  data: number[];
  width?: number;
  height?: number;
  color?: string;
  className?: string;
  showArea?: boolean;
}

export function Sparkline({ data, width = 80, height = 28, color, showArea = true, className }: SparklineProps) {
  const path = useMemo(() => {
    if (!data || data.length < 2) return { line: "", area: "" };
    const min = Math.min(...data);
    const max = Math.max(...data);
    const range = max - min || 1;
    const xStep = width / (data.length - 1);
    const points = data.map((v, i) => ({
      x: i * xStep,
      y: height - ((v - min) / range) * (height - 2) - 1,
    }));
    const line = points.map((p, i) => `${i === 0 ? "M" : "L"}${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(" ");
    const area = `${line} L${width},${height} L0,${height} Z`;
    return { line, area };
  }, [data, width, height]);

  const isPositive = data.length >= 2 && data[data.length - 1] >= data[0];
  const strokeColor = color ?? (isPositive ? "#10b981" : "#ef4444");
  const fillColor   = color ?? (isPositive ? "rgba(16,185,129,0.12)" : "rgba(239,68,68,0.12)");

  if (!data || data.length < 2) {
    return <div className={cn("skeleton", className)} style={{ width, height }} />;
  }

  return (
    <svg width={width} height={height} className={cn("overflow-visible", className)}>
      {showArea && <path d={path.area} fill={fillColor} />}
      <path d={path.line} fill="none" stroke={strokeColor} strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
