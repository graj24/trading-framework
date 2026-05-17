import type { SignalScores } from "@/lib/api";
import { fmt } from "@/lib/formatters";
import { Badge } from "@/components/ui/Badge";
import { cn } from "@/lib/cn";
import { Play, RefreshCw } from "lucide-react";

interface Props {
  signal?: SignalScores;
  loading?: boolean;
  onRun?: () => void;
}

function ScoreRow({ label, value, max = 10, color = "bg-accent-primary" }: {
  label: string; value?: number | null; max?: number; color?: string;
}) {
  const pct = value != null ? Math.min(Math.max((value / max) * 100, 0), 100) : 0;
  return (
    <div className="flex items-center gap-2">
      <span className="text-content-muted text-xs w-20 shrink-0">{label}</span>
      <div className="flex-1 signal-bar">
        <div className={cn("signal-bar-fill", color)} style={{ width: `${pct}%` }} />
      </div>
      <span className="num text-xs text-content-secondary w-8 text-right">
        {value != null ? value.toFixed(1) : "—"}
      </span>
    </div>
  );
}

// Radial confidence gauge
function ConfidenceGauge({ value }: { value: number }) {
  const r = 28, cx = 36, cy = 36;
  const circumference = 2 * Math.PI * r;
  const pct = Math.min(Math.max(value, 0), 100) / 100;
  const dash = pct * circumference;
  const color = value >= 70 ? "#10b981" : value >= 50 ? "#f59e0b" : "#ef4444";

  return (
    <div className="flex flex-col items-center gap-1">
      <svg width={72} height={72} className="-rotate-90">
        <circle cx={cx} cy={cy} r={r} fill="none" stroke="#1f2937" strokeWidth={5} />
        <circle
          cx={cx} cy={cy} r={r} fill="none"
          stroke={color} strokeWidth={5}
          strokeDasharray={`${dash} ${circumference}`}
          strokeLinecap="round"
          style={{ transition: "stroke-dasharray 0.6s ease" }}
        />
      </svg>
      <div className="absolute" style={{ marginTop: -52 }}>
        <span className="num text-lg font-bold" style={{ color }}>{value.toFixed(0)}</span>
        <span className="text-xs text-content-muted">%</span>
      </div>
    </div>
  );
}

const DECISION_BADGE: Record<string, "success" | "danger" | "warning" | "default"> = {
  BUY: "success", SELL: "danger", HOLD: "warning",
};

export function SignalPanel({ signal, loading, onRun }: Props) {
  const decision = signal?.decision ?? "—";
  const badgeVariant = DECISION_BADGE[decision] ?? "default";

  return (
    <div className="flex flex-col h-full bg-surface-900">
      <div className="panel-header border-b border-surface-700">
        <span className="panel-title">Signal</span>
        {onRun && (
          <button
            onClick={onRun}
            disabled={loading}
            className="btn-sm btn-ghost flex items-center gap-1"
          >
            {loading ? <RefreshCw size={11} className="animate-spin" /> : <Play size={11} />}
            {loading ? "Running…" : "Run"}
          </button>
        )}
      </div>

      <div className="flex-1 overflow-y-auto p-3 flex flex-col gap-4">
        {/* Price */}
        {signal?.price && (
          <div className="num text-2xl font-bold text-content-primary">
            {fmt.inr(signal.price)}
          </div>
        )}

        {/* Decision + confidence */}
        <div className="flex items-center gap-3">
          <Badge variant={badgeVariant} className="text-sm px-3 py-1 font-bold">
            {decision}
          </Badge>
          {signal?.confidence != null && (
            <div className="relative flex items-center justify-center" style={{ width: 72, height: 72 }}>
              <ConfidenceGauge value={signal.confidence} />
              <div className="absolute inset-0 flex flex-col items-center justify-center">
                <span className={cn("num text-base font-bold",
                  signal.confidence >= 70 ? "text-accent-success" :
                  signal.confidence >= 50 ? "text-accent-warning" : "text-accent-danger"
                )}>
                  {signal.confidence.toFixed(0)}
                </span>
                <span className="text-[9px] text-content-muted">CONF%</span>
              </div>
            </div>
          )}
        </div>

        {/* Score bars */}
        <div className="flex flex-col gap-2.5">
          <ScoreRow label="Technical" value={signal?.technical_score} max={10} color="bg-accent-primary" />
          <ScoreRow
            label="Sentiment"
            value={signal?.sentiment != null ? (signal.sentiment + 1) * 5 : null}
            max={10}
            color="bg-accent-info"
          />
          <ScoreRow
            label="Pattern EV"
            value={signal?.pattern_ev != null ? Math.abs(signal.pattern_ev) : null}
            max={5}
            color="bg-chart-3"
          />
          <ScoreRow
            label="ML Proba"
            value={signal?.ml_proba != null ? Number(signal.ml_proba) * 10 : null}
            max={10}
            color="bg-accent-purple"
          />
        </div>

        {/* Regime */}
        {signal?.regime && (
          <div className="flex items-center gap-2">
            <span className="text-xs text-content-muted">Regime</span>
            <Badge variant={signal.regime.includes("bull") ? "success" : signal.regime.includes("bear") ? "danger" : "warning"} dot>
              {signal.regime.replace(/_/g, " ").toUpperCase()}
            </Badge>
          </div>
        )}

        {/* Reasoning */}
        {signal?.reasoning && (
          <div className="bg-surface-800 rounded-lg p-2.5 border border-surface-700">
            <div className="text-[10px] text-content-muted uppercase tracking-wider mb-1">LLM Reasoning</div>
            <p className="text-xs text-content-secondary leading-relaxed">{signal.reasoning}</p>
          </div>
        )}

        {!signal && !loading && (
          <div className="flex-1 flex flex-col items-center justify-center gap-2 text-content-muted">
            <Play size={24} className="opacity-30" />
            <span className="text-xs">Select a symbol and run signal</span>
          </div>
        )}
      </div>
    </div>
  );
}
