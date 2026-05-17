import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ServiceHealth } from "@/lib/api";
import {
  RefreshCw, CheckCircle, XCircle, AlertTriangle,
  MinusCircle, ChevronDown, ChevronRight, ExternalLink, Save,
} from "lucide-react";
import { cn } from "@/lib/cn";

// ── service catalogue ─────────────────────────────────────────────────────────

const SERVICES = [
  {
    id: "groq",
    label: "Groq (LLM — active)",
    description: "Primary LLM provider. Powers Strategist brain, master agent, triage daemon. Models: llama-3.3-70b (heartbeat), llama-3.1-8b (off-shift).",
    docsUrl: "https://console.groq.com",
    envKeys: [{ key: "GROQ_API_KEY", label: "API Key", type: "password" as const }],
  },
  {
    id: "openai",
    label: "OpenAI (LLM — optional)",
    description: "Alternate LLM provider. Used when llm.model in config.yaml starts with openai/ (e.g. openai/gpt-4o-mini).",
    docsUrl: "https://platform.openai.com/account/api-keys",
    envKeys: [{ key: "OPENAI_API_KEY", label: "API Key", type: "password" as const }],
  },
  {
    id: "anthropic",
    label: "Anthropic (LLM — optional)",
    description: "Alternate LLM provider. Used when llm.model starts with anthropic/ (e.g. anthropic/claude-3-5-sonnet-20241022).",
    docsUrl: "https://console.anthropic.com/settings/keys",
    envKeys: [{ key: "ANTHROPIC_API_KEY", label: "API Key", type: "password" as const }],
  },
  {
    id: "bedrock",
    label: "AWS Bedrock (LLM — optional)",
    description: "Alternate LLM provider via AWS. Used when llm.model starts with bedrock/. Requires IAM bedrock:InvokeModel permission.",
    docsUrl: "https://console.aws.amazon.com/bedrock",
    envKeys: [
      { key: "AWS_ACCESS_KEY_ID",     label: "Access Key ID",     type: "password" as const },
      { key: "AWS_SECRET_ACCESS_KEY", label: "Secret Access Key", type: "password" as const },
      { key: "AWS_REGION",            label: "Region",            type: "text" as const },
    ],
  },
  {
    id: "yfinance",
    label: "Yahoo Finance",
    description: "Primary OHLCV data source for all technical analysis. No credentials needed.",
    docsUrl: "https://pypi.org/project/yfinance/",
    envKeys: [],
  },
  {
    id: "groww",
    label: "Groww",
    description: "Real-time LTP feed. Used as primary price source; yfinance is fallback.",
    docsUrl: "https://groww.in/user/profile/trading-apis",
    envKeys: [
      { key: "GROWW_API_KEY",      label: "API Key",      type: "password" as const },
      { key: "GROWW_SECRET",       label: "Secret",       type: "password" as const },
      { key: "GROWW_ACCESS_TOKEN", label: "Access Token (daily)", type: "password" as const },
    ],
    note: "Access tokens expire daily at 6 AM. Run python -m core.groww_client to refresh.",
  },
  {
    id: "telegram",
    label: "Telegram",
    description: "Alert delivery — trade signals, P&L alerts, anomaly notifications.",
    docsUrl: "https://core.telegram.org/bots",
    envKeys: [
      { key: "TELEGRAM_BOT_TOKEN", label: "Bot Token",  type: "password" as const },
      { key: "TELEGRAM_CHAT_ID",   label: "Chat ID",    type: "text" as const },
    ],
  },
  {
    id: "zerodha",
    label: "Zerodha (Kite)",
    description: "Live broker — only required when trading.mode = live. Unused in paper mode.",
    docsUrl: "https://kite.trade/docs/connect/v3/",
    envKeys: [
      { key: "ZERODHA_API_KEY",      label: "API Key",      type: "password" as const },
      { key: "ZERODHA_API_SECRET",   label: "API Secret",   type: "password" as const },
      { key: "ZERODHA_ACCESS_TOKEN", label: "Access Token (daily)", type: "password" as const },
    ],
    note: "Access tokens expire daily. Regenerate via the Kite login flow.",
  },
  {
    id: "twitter",
    label: "Twitter / X",
    description: "Optional — DiscoveryAgent uses Nitter scraping by default. Twitter API keys unlock direct search.",
    docsUrl: "https://developer.twitter.com",
    envKeys: [
      { key: "TWITTER_BEARER_TOKEN", label: "Bearer Token", type: "password" as const },
    ],
  },
] as const;

// ── status helpers ────────────────────────────────────────────────────────────

function StatusIcon({ status }: { status: ServiceHealth["status"] }) {
  if (status === "ok")           return <CheckCircle  size={16} className="text-green-400 shrink-0" />;
  if (status === "error")        return <XCircle      size={16} className="text-red-400 shrink-0" />;
  if (status === "degraded")     return <AlertTriangle size={16} className="text-yellow-400 shrink-0" />;
  if (status === "unconfigured") return <AlertTriangle size={16} className="text-orange-400 shrink-0" />;
  return                                <MinusCircle  size={16} className="text-surface-500 shrink-0" />;
}

function statusColor(status: ServiceHealth["status"]) {
  if (status === "ok")           return "border-green-500/30 bg-green-500/5";
  if (status === "error")        return "border-red-500/30 bg-red-500/5";
  if (status === "degraded")     return "border-yellow-500/30 bg-yellow-500/5";
  if (status === "unconfigured") return "border-orange-500/30 bg-orange-500/5";
  return "border-surface-700 bg-surface-800/30";
}

function statusLabel(status: ServiceHealth["status"]) {
  const map: Record<string, string> = {
    ok: "Healthy", error: "Error", degraded: "Degraded",
    unconfigured: "Not configured", not_required: "Not required",
  };
  return map[status] ?? status;
}

// ── env editor ────────────────────────────────────────────────────────────────

function EnvEditor({
  envKeys, envStatus, onSaved,
}: {
  envKeys: readonly { key: string; label: string; type: "password" | "text" }[];
  envStatus: Record<string, string>;
  onSaved: () => void;
}) {
  const [vals, setVals] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  if (envKeys.length === 0) return null;

  const handleSave = async () => {
    const toSave = Object.fromEntries(
      Object.entries(vals).filter(([, v]) => v.trim() !== "")
    );
    if (Object.keys(toSave).length === 0) return;
    setSaving(true);
    try {
      await api.patchEnv(toSave);
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
      onSaved();
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="mt-3 space-y-2">
      {envKeys.map(({ key, label, type }) => {
        const current = envStatus[key];
        const isSet = current && current !== "";
        return (
          <div key={key} className="flex flex-col gap-1">
            <div className="flex items-center gap-2">
              <label className="text-[11px] text-content-muted w-36 shrink-0">{label}</label>
              <span className={cn("text-[10px] px-1.5 py-0.5 rounded font-mono",
                isSet ? "bg-green-500/15 text-green-300" : "bg-orange-500/15 text-orange-300"
              )}>
                {isSet ? (current === "***set***" ? "●●●●●● set" : current) : "not set"}
              </span>
            </div>
            <input
              type={type}
              placeholder={`New value for ${key}`}
              value={vals[key] ?? ""}
              onChange={(e) => setVals((p) => ({ ...p, [key]: e.target.value }))}
              className="w-full bg-surface-900 border border-surface-700 rounded px-2.5 py-1.5 text-xs font-mono text-content-primary focus:outline-none focus:border-accent-primary transition-colors"
            />
          </div>
        );
      })}
      <button
        onClick={handleSave}
        disabled={saving || Object.values(vals).every((v) => !v.trim())}
        className={cn(
          "flex items-center gap-1.5 text-xs px-3 py-1.5 rounded transition-colors",
          saved
            ? "bg-green-500/20 text-green-300"
            : "bg-accent-primary/20 text-accent-primary hover:bg-accent-primary/30 disabled:opacity-40 disabled:cursor-not-allowed"
        )}
      >
        <Save size={12} />
        {saved ? "Saved!" : saving ? "Saving…" : "Save to .env"}
      </button>
    </div>
  );
}

// ── service card ──────────────────────────────────────────────────────────────

function ServiceCard({
  svc, health, envStatus, onRecheck,
}: {
  svc: typeof SERVICES[number];
  health: ServiceHealth | undefined;
  envStatus: Record<string, string>;
  onRecheck: () => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const status = health?.status ?? "not_required";
  const showExpand = status !== "ok" && status !== "not_required";

  return (
    <div className={cn("border rounded-lg overflow-hidden transition-colors", statusColor(status))}>
      {/* header row */}
      <div className="flex items-center gap-3 px-4 py-3">
        <StatusIcon status={status} />
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-sm font-semibold text-content-primary">{svc.label}</span>
            <span className={cn("text-[10px] px-1.5 py-0.5 rounded font-medium",
              status === "ok"           ? "bg-green-500/20 text-green-300" :
              status === "error"        ? "bg-red-500/20 text-red-300" :
              status === "degraded"     ? "bg-yellow-500/20 text-yellow-300" :
              status === "unconfigured" ? "bg-orange-500/20 text-orange-300" :
              "bg-surface-700 text-content-muted"
            )}>
              {statusLabel(status)}
            </span>
            {health?.latency_ms !== undefined && (
              <span className="text-[10px] text-content-muted">{health.latency_ms}ms</span>
            )}
          </div>
          <p className="text-xs text-content-muted mt-0.5 truncate">{svc.description}</p>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          {health?.sample && (
            <span className="text-[10px] text-green-300 font-mono hidden md:block">{health.sample}</span>
          )}
          {health?.bot && (
            <span className="text-[10px] text-green-300 font-mono hidden md:block">{health.bot}</span>
          )}
          {health?.user && (
            <span className="text-[10px] text-green-300 font-mono hidden md:block">{health.user}</span>
          )}
          <a href={svc.docsUrl} target="_blank" rel="noreferrer"
            className="text-content-muted hover:text-content-primary transition-colors">
            <ExternalLink size={13} />
          </a>
          {showExpand && (
            <button onClick={() => setExpanded(!expanded)}
              className="text-content-muted hover:text-content-primary transition-colors">
              {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
            </button>
          )}
        </div>
      </div>

      {/* expanded: error + fix + env editor */}
      {(showExpand && expanded) && (
        <div className="px-4 pb-4 border-t border-surface-700/50 pt-3 space-y-3">
          {health?.error && (
            <div className="text-xs text-red-300 bg-red-500/10 rounded px-3 py-2 font-mono break-all">
              {health.error}
            </div>
          )}
          {health?.fix && (
            <div className="text-xs text-content-secondary bg-surface-800 rounded px-3 py-2">
              <span className="text-yellow-300 font-semibold">How to fix: </span>{health.fix}
            </div>
          )}
          {"note" in svc && svc.note && (
            <div className="text-xs text-content-muted italic">{svc.note}</div>
          )}
          {svc.envKeys.length > 0 && (
            <EnvEditor envKeys={svc.envKeys} envStatus={envStatus} onSaved={onRecheck} />
          )}
        </div>
      )}

      {/* not_required detail */}
      {status === "not_required" && health?.detail && (
        <div className="px-4 pb-3 text-xs text-content-muted">{health.detail}</div>
      )}
    </div>
  );
}

// ── page ──────────────────────────────────────────────────────────────────────

export function Services() {
  const qc = useQueryClient();
  const { data: health, isLoading, dataUpdatedAt, refetch, isFetching } =
    useQuery<Record<string, ServiceHealth>>({
      queryKey: ["servicesHealth"],
      queryFn: api.servicesHealth,
      refetchInterval: 60_000,
      retry: false,
    });

  const { data: envStatus = {} } = useQuery<Record<string, string>>({
    queryKey: ["envStatus"],
    queryFn: api.envStatus,
  });

  const recheck = () => {
    qc.invalidateQueries({ queryKey: ["servicesHealth"] });
    qc.invalidateQueries({ queryKey: ["envStatus"] });
    refetch();
  };

  const statuses = health ? Object.values(health).map((h) => h.status) : [];
  const nOk    = statuses.filter((s) => s === "ok").length;
  const nError = statuses.filter((s) => s === "error" || s === "degraded").length;
  const nWarn  = statuses.filter((s) => s === "unconfigured").length;

  return (
    <div className="flex-1 overflow-y-auto p-6 max-w-3xl mx-auto w-full">
      {/* header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-xl font-bold text-content-primary">Services</h1>
          <p className="text-sm text-content-muted mt-1">
            Health of every external integration. Click a card to see errors and fix instructions.
          </p>
        </div>
        <div className="flex items-center gap-3">
          {dataUpdatedAt > 0 && (
            <span className="text-[11px] text-content-muted">
              Updated {new Date(dataUpdatedAt).toLocaleTimeString()}
            </span>
          )}
          <button onClick={recheck} disabled={isFetching}
            className="flex items-center gap-1.5 text-xs px-3 py-1.5 rounded bg-surface-700 hover:bg-surface-600 text-content-secondary transition-colors disabled:opacity-50">
            <RefreshCw size={12} className={cn(isFetching && "animate-spin")} />
            Recheck
          </button>
        </div>
      </div>

      {/* summary bar */}
      {!isLoading && health && (
        <div className="flex gap-4 mb-5 text-sm">
          <span className="text-green-400 font-medium">{nOk} healthy</span>
          {nError > 0 && <span className="text-red-400 font-medium">{nError} error{nError > 1 ? "s" : ""}</span>}
          {nWarn > 0  && <span className="text-orange-400 font-medium">{nWarn} not configured</span>}
        </div>
      )}

      {isLoading ? (
        <div className="flex items-center gap-2 text-content-muted text-sm py-12 justify-center">
          <RefreshCw size={14} className="animate-spin" /> Checking services…
        </div>
      ) : (
        <div className="space-y-3">
          {SERVICES.map((svc) => (
            <ServiceCard
              key={svc.id}
              svc={svc}
              health={health?.[svc.id]}
              envStatus={envStatus}
              onRecheck={recheck}
            />
          ))}
        </div>
      )}
    </div>
  );
}
