import { useAlertStore } from "@/store/useAlertStore";
import clsx from "clsx";

const SEV_COLORS = {
  info: "bg-blue/10 border-blue/30 text-blue",
  warn: "bg-gold/10 border-gold/30 text-gold",
  error: "bg-red/10 border-red/30 text-red",
};

export function AlertBanner() {
  const { alerts, dismiss } = useAlertStore();
  if (!alerts.length) return null;

  return (
    <div className="flex flex-col gap-0.5 px-2 py-1 shrink-0">
      {alerts.slice(0, 3).map((a) => (
        <div
          key={a.id}
          className={clsx("flex items-center gap-2 px-2 py-0.5 rounded border text-xs", SEV_COLORS[a.severity])}
        >
          <span className="flex-1">{a.message}</span>
          <button onClick={() => dismiss(a.id)} className="opacity-50 hover:opacity-100">✕</button>
        </div>
      ))}
    </div>
  );
}
