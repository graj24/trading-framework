import { AlertTriangle, Info, XCircle, X } from "lucide-react";
import { useAlertStore } from "@/store/useAlertStore";
import { cn } from "@/lib/cn";

const SEV = {
  info:  { cls: "bg-accent-info/10 border-accent-info/30 text-accent-info",    Icon: Info },
  warn:  { cls: "bg-accent-warning/10 border-accent-warning/30 text-accent-warning", Icon: AlertTriangle },
  error: { cls: "bg-accent-danger/10 border-accent-danger/30 text-accent-danger",  Icon: XCircle },
};

export function AlertBanner() {
  const { alerts, dismiss } = useAlertStore();
  if (!alerts.length) return null;

  return (
    <div className="flex flex-col gap-0.5 px-2 py-1 shrink-0 bg-surface-950 border-b border-surface-700">
      {alerts.slice(0, 3).map((a) => {
        const { cls, Icon } = SEV[a.severity];
        return (
          <div key={a.id} className={cn("flex items-center gap-2 px-2.5 py-1 rounded-md border text-xs animate-slide-down", cls)}>
            <Icon size={12} className="shrink-0" />
            <span className="flex-1">{a.message}</span>
            <button onClick={() => dismiss(a.id)} className="opacity-50 hover:opacity-100 transition-opacity">
              <X size={12} />
            </button>
          </div>
        );
      })}
    </div>
  );
}
