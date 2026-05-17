import type { SignalScores } from "@/lib/api";
import { fmt } from "@/lib/formatters";
import clsx from "clsx";

interface Props {
  signal?: SignalScores;
  loading?: boolean;
  onRun?: () => void;
}

const DECISION_COLORS: Record<string, string> = {
  BUY: "bg-green/20 text-green border-green/40",
  SELL: "bg-red/20 text-red border-red/40",
  HOLD: "bg-gold/20 text-gold border-gold/40",
  ERROR: "bg-bg-tertiary text-text-muted border-border",
};

function ScoreBar({ label, value, max = 10 }: { label: string; value?: number | null; max?: number }) {
  const pct = value != null ? Math.min(Math.max((value / max) * 100, 0), 100) : 0;
  return (
    <div className="flex items-center gap-2 text-xs">
      <span className="text-text-muted w-16 shrink-0">{label}</span>
      <div className="flex-1 h-1.5 bg-bg-tertiary rounded-full overflow-hidden">
        <div
          className="h-full bg-blue rounded-full transition-all duration-500"
          style={{ width: `${pct}%` }}
        />
      </div>
      <span className="mono text-text-secondary w-10 text-right">
        {value != null ? value.toFixed(1) : "—"}
      </span>
    </div>
  );
}

export function SignalPanel({ signal, loading, onRun }: Props) {
  const decision = signal?.decision ?? "—";
  const decColor = DECISION_COLORS[decision] ?? DECISION_COLORS.ERROR;

  return (
    <div className="flex flex-col h-full">
      <div className="px-3 py-1.5 border-b border-border text-xs text-text-secondary font-semibold tracking-wider flex items-center justify-between">
        <span>SIGNAL PANEL</span>
        {onRun && (
          <button
            onClick={onRun}
            disabled={loading}
            className="text-blue hover:text-blue/80 disabled:opacity-40 text-xs"
          >
            {loading ? "Running…" : "▶ Run"}
          </button>
        )}
      </div>

      <div className="flex-1 overflow-auto p-3 flex flex-col gap-3">
        {/* Price */}
        {signal?.price && (
          <div className="mono text-xl font-bold text-text-primary">
            {fmt.inr(signal.price)}
          </div>
        )}

        {/* Decision badge */}
        <div className={clsx("px-3 py-1.5 rounded border text-sm font-bold text-center", decColor)}>
          {decision}
          {signal?.confidence != null && (
            <span className="ml-2 text-xs font-normal opacity-70">{signal.confidence.toFixed(0)}%</span>
          )}
        </div>

        {/* Score bars */}
        <div className="flex flex-col gap-2">
          <ScoreBar label="Technical" value={signal?.technical_score} max={10} />
          <ScoreBar label="Sentiment" value={signal?.sentiment != null ? (signal.sentiment + 1) * 5 : null} max={10} />
          <ScoreBar label="Pattern EV" value={signal?.pattern_ev != null ? Math.abs(signal.pattern_ev) : null} max={5} />
          <ScoreBar label="ML Proba" value={signal?.ml_proba != null ? Number(signal.ml_proba) * 10 : null} max={10} />
        </div>

        {/* Regime */}
        {signal?.regime && (
          <div className="text-xs text-text-muted">
            Regime: <span className="text-gold">{signal.regime.replace(/_/g, " ").toUpperCase()}</span>
          </div>
        )}

        {/* Reasoning */}
        {signal?.reasoning && (
          <div className="text-xs text-text-secondary bg-bg-tertiary rounded p-2 leading-relaxed">
            {signal.reasoning}
          </div>
        )}
      </div>
    </div>
  );
}
