// Animated number that ticks when value changes
import { useEffect, useRef, useState } from "react";
import { cn } from "@/lib/cn";

interface AnimatedNumberProps {
  value: number;
  format?: (v: number) => string;
  className?: string;
  colorize?: boolean; // green if up, red if down
}

export function AnimatedNumber({ value, format, className, colorize }: AnimatedNumberProps) {
  const prev = useRef(value);
  const [flash, setFlash] = useState<"up" | "down" | null>(null);

  useEffect(() => {
    if (value !== prev.current) {
      setFlash(value > prev.current ? "up" : "down");
      prev.current = value;
      const t = setTimeout(() => setFlash(null), 600);
      return () => clearTimeout(t);
    }
  }, [value]);

  const display = format ? format(value) : value.toLocaleString("en-IN", { maximumFractionDigits: 2 });

  return (
    <span className={cn(
      "num transition-colors duration-300",
      colorize && value > 0 && "text-accent-success",
      colorize && value < 0 && "text-accent-danger",
      flash === "up"   && "text-accent-success",
      flash === "down" && "text-accent-danger",
      className
    )}>
      {display}
    </span>
  );
}
