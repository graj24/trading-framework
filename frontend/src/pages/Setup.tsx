import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Badge } from "@/components/ui/Badge";
import { cn } from "@/lib/cn";
import { CheckCircle, XCircle, ChevronRight, ChevronLeft, Save } from "lucide-react";

const STEPS = ["LLM", "Market Data", "Alerts", "Broker", "Review"];

function EnvField({ label, envKey, type = "password", placeholder, value, onChange }: {
  label: string; envKey: string; type?: string; placeholder?: string; value: string; onChange: (v: string) => void;
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <label className="text-xs text-content-muted">{label}</label>
      <input
        type={type}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder ?? envKey}
        className="bg-surface-800 border border-surface-700 rounded-lg px-3 py-2 text-xs text-content-primary font-mono focus:outline-none focus:border-accent-primary transition-colors"
      />
    </div>
  );
}

export function Setup() {
  const [step, setStep] = useState(0);
  const [vals, setVals] = useState<Record<string, string>>({});
  const [testing, setTesting] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<Record<string, { status: string; error?: string }>>({});
  const [saved, setSaved] = useState(false);

  const { data: envStatus } = useQuery({ queryKey: ["envStatus"], queryFn: api.envStatus });
  const set = (k: string) => (v: string) => setVals((prev) => ({ ...prev, [k]: v }));

  const test = async (service: string) => {
    setTesting(service);
    const r = await api.testService(service);
    setTestResult((prev) => ({ ...prev, [service]: r }));
    setTesting(null);
  };

  const save = async () => {
    const toSave = Object.fromEntries(Object.entries(vals).filter(([, v]) => v.trim()));
    if (Object.keys(toSave).length) await api.patchEnv(toSave);
    setSaved(true);
  };

  const TestBadge = ({ service }: { service: string }) => {
    const r = testResult[service];
    if (!r) return null;
    return r.status === "ok"
      ? <Badge variant="success"><CheckCircle size={10} /> Connected</Badge>
      : <Badge variant="danger"><XCircle size={10} /> {r.error ?? "Failed"}</Badge>;
  };

  const stepContent = [
    <div key="llm" className="flex flex-col gap-4">
      <p className="text-xs text-content-secondary">Configure your LLM provider. Groq is recommended — fast and free tier available.</p>
      <EnvField label="Groq API Key" envKey="GROQ_API_KEY" value={vals.GROQ_API_KEY ?? ""} onChange={set("GROQ_API_KEY")} />
      {envStatus?.GROQ_API_KEY === "***set***" && <Badge variant="success" dot>Key already configured</Badge>}
      <div className="flex items-center gap-3">
        <button onClick={() => test("groq")} disabled={testing === "groq"} className="btn-sm btn-ghost">
          {testing === "groq" ? "Testing…" : "Test connection"}
        </button>
        <TestBadge service="groq" />
      </div>
    </div>,

    <div key="market" className="flex flex-col gap-4">
      <p className="text-xs text-content-secondary">Market data uses yfinance by default (no key needed). Groww provides real-time NSE data.</p>
      <EnvField label="Groww Access Token (optional)" envKey="GROWW_ACCESS_TOKEN" value={vals.GROWW_ACCESS_TOKEN ?? ""} onChange={set("GROWW_ACCESS_TOKEN")} />
      <div className="flex items-center gap-3">
        <button onClick={() => test("market")} disabled={testing === "market"} className="btn-sm btn-ghost">
          {testing === "market" ? "Testing…" : "Test RELIANCE LTP"}
        </button>
        <TestBadge service="market" />
      </div>
    </div>,

    <div key="alerts" className="flex flex-col gap-4">
      <p className="text-xs text-content-secondary">Telegram alerts for trade signals and anomalies.</p>
      <EnvField label="Telegram Bot Token" envKey="TELEGRAM_BOT_TOKEN" value={vals.TELEGRAM_BOT_TOKEN ?? ""} onChange={set("TELEGRAM_BOT_TOKEN")} />
      <EnvField label="Telegram Chat ID" envKey="TELEGRAM_CHAT_ID" type="text" value={vals.TELEGRAM_CHAT_ID ?? ""} onChange={set("TELEGRAM_CHAT_ID")} />
      <div className="flex items-center gap-3">
        <button onClick={() => test("telegram")} disabled={testing === "telegram"} className="btn-sm btn-ghost">
          {testing === "telegram" ? "Sending…" : "Send test message"}
        </button>
        <TestBadge service="telegram" />
      </div>
    </div>,

    <div key="broker" className="flex flex-col gap-4">
      <p className="text-xs text-content-secondary">Choose trading mode. Paper mode requires no broker credentials.</p>
      {(["paper", "shadow", "live"] as const).map((mode) => (
        <button
          key={mode}
          onClick={() => set("TRADING_MODE")(mode)}
          className={cn("text-xs px-4 py-3 rounded-xl border text-left transition-all",
            vals.TRADING_MODE === mode
              ? "bg-accent-primary/15 border-accent-primary/40 text-accent-primary"
              : "border-surface-700 text-content-muted hover:border-surface-600"
          )}
        >
          <div className="font-semibold capitalize mb-0.5">{mode}</div>
          <div className="text-content-muted text-[11px]">
            {mode === "paper" ? "Simulated trades, no real money" : mode === "shadow" ? "Signals only, no execution" : "Real broker execution via Zerodha"}
          </div>
        </button>
      ))}
    </div>,

    <div key="review" className="flex flex-col gap-2">
      <p className="text-xs text-content-secondary mb-2">Review your configuration before saving.</p>
      {Object.entries(envStatus ?? {}).map(([k, v]) => (
        <div key={k} className="flex justify-between items-center py-1.5 border-b border-surface-700/50">
          <span className="text-xs text-content-muted font-mono">{k}</span>
          <Badge variant={v === "***set***" ? "success" : v ? "default" : "default"}>
            {v === "***set***" ? "✓ set" : v || "not set"}
          </Badge>
        </div>
      ))}
      {saved && <Badge variant="success" dot className="mt-2">Configuration saved to .env</Badge>}
    </div>,
  ];

  return (
    <div className="flex h-full items-center justify-center bg-surface-900">
      <div className="w-[520px] panel overflow-hidden">
        {/* Step tabs */}
        <div className="flex border-b border-surface-700">
          {STEPS.map((s, i) => (
            <button
              key={s}
              onClick={() => setStep(i)}
              className={cn(
                "flex-1 py-2.5 text-xs font-medium transition-colors",
                i === step ? "bg-accent-primary/10 text-accent-primary border-b-2 border-accent-primary" :
                i < step  ? "text-accent-success" : "text-content-muted"
              )}
            >
              {i < step ? "✓ " : ""}{s}
            </button>
          ))}
        </div>

        <div className="p-6 min-h-[280px]">
          <h2 className="text-sm font-bold text-content-primary mb-4">{STEPS[step]}</h2>
          {stepContent[step]}
        </div>

        <div className="flex justify-between px-6 py-4 border-t border-surface-700">
          <button
            onClick={() => setStep((s) => Math.max(0, s - 1))}
            disabled={step === 0}
            className="btn-sm btn-ghost disabled:opacity-30"
          >
            <ChevronLeft size={13} /> Back
          </button>
          {step < STEPS.length - 1 ? (
            <button onClick={() => setStep((s) => s + 1)} className="btn-sm btn-primary">
              Next <ChevronRight size={13} />
            </button>
          ) : (
            <button onClick={save} className="btn-sm btn-success">
              <Save size={13} /> Save Configuration
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
