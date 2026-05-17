import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import clsx from "clsx";

function heatColor(v: number | null) {
  if (v == null) return "bg-bg-tertiary text-text-muted";
  if (v > 3) return "bg-green/30 text-green";
  if (v > 1) return "bg-green/15 text-green";
  if (v > 0) return "bg-green/5 text-green";
  if (v > -1) return "bg-red/5 text-red";
  if (v > -3) return "bg-red/15 text-red";
  return "bg-red/30 text-red";
}

export function SectorHeatmap() {
  const { data } = useQuery({ queryKey: ["sectors"], queryFn: api.sectors, refetchInterval: 60000 });

  return (
    <div className="flex flex-col h-full">
      <div className="px-3 py-1.5 border-b border-border text-xs text-text-secondary font-semibold tracking-wider">
        SECTOR RETURNS (30D)
      </div>
      <div className="flex-1 p-2 grid grid-cols-2 gap-1 content-start overflow-auto">
        {Object.entries(data ?? {}).map(([name, val]) => (
          <div key={name} className={clsx("rounded p-2 text-xs flex justify-between items-center", heatColor(val))}>
            <span className="font-semibold">{name}</span>
            <span className="mono">{val != null ? (val >= 0 ? "+" : "") + val.toFixed(1) + "%" : "—"}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
