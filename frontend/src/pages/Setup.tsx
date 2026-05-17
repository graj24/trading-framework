import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import clsx from "clsx";

const STEPS = ["LLM", "Market Data", "Alerts", "Broker", "Review"];

interface FieldProps {
  label: string;
  envKey: string;
  type?: string;
  placeholder?: string;
  value: string;
  onChange: (v: string) => void;
}

function EnvField({ label, envKey, type = "password", placeholder, value, onChange }: FieldProps) {
  return (
    <div className="flex flex-col gap-1">
      <label className="text-xs text-text-muted">{label}</label>
      <input
        type={type}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder ?? envKey}
        className="bg-bg-tertiary border border-border rounded px-3 py-1.5 text-xs text-text-primary mono focus:border-blue outline-none"
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

  const statusBadge = (service: string) => {
    const r = testResult[service];
    if (!r) return null;
    return (
      <span className={clsx("text-xs px-1.5 py-0.5 rounded", r.status === "ok" ? "bg-green/20 text-green" : "bg-red/20 text-red")}>
        {r.status === "ok" ? "✓ Connected" : "✗ " + (r.error ?? "Failed")}
      </span>
    );
  };

  const stepContent = [
    // Step 0: LLM
    <div key="llm" className="flex flex-col gap-4">
      <p className="text-xs text-text-secondary">Configure your LLM provider (Groq is recommended — fast and free tier available).</p>
      <EnvField label="Groq API Key" envKey="GROQ_API_KEY" value={vals.GROQ_API_KEY ?? ""} onChange={set("GROQ_API_KEY")} />
      {envStatus?.GROQ_API_KEY === "***set***" && <div className="text-xs text-green">✓ Key already configured</div>}
      <div className="flex items-center gap-3">
        <button onClick={() => test("groq")} disabled={testing === "groq"} className="text-xs text-blue hover:underline disabled:opacity-40">
          {testing === "groq" ? "Testing…" : "Test connection"}
        </button>
        {statusBadge("groq")}
      </div>
    </div>,

    // Step 1: Market data
    <div key="market" className="flex flex-col gap-4">
      <p className="text-xs text-text-secondary">Market data uses yfinance by default (no key needed). Groww provides real-time NSE data.</p>
      <EnvField label="Groww Access Token (optional)" envKey="GROWW_ACCESS_TOKEN" value={vals.GROWW_ACCESS_TOKEN ?? ""} onChange={set("GROWW_ACCESS_TOKEN")} />
      <div className="flex items-center gap-3">
        <button onClick={() => test("market")} disabled={testing === "market"} className="text-xs text-blue hover:underline disabled:opacity-40">
          {testing === "market" ? "Testing…" : "Test RELIANCE LTP"}
        </button>
        {statusBadge("market")}
      </div>
    </div>,

    // Step 2: Alerts
    <div key="alerts" className="flex flex-col gap-4">
      <p className="text-xs text-text-secondary">Telegram alerts for trade signals and anomalies.</p>
      <EnvField label="Telegram Bot Token" envKey="TELEGRAM_BOT_TOKEN" value={vals.TELEGRAM_BOT_TOKEN ?? ""} onChange={set("TELEGRAM_BOT_TOKEN")} />
      <EnvField label="Telegram Chat ID" envKey="TELEGRAM_CHAT_ID" type="text" value={vals.TELEGRAM_CHAT_ID ?? ""} onChange={set("TELEGRAM_CHAT_ID")} />
      <div className="flex items-center gap-3">
        <button onClick={() => test("telegram")} disabled={testing === "telegram"} className="text-xs text-blue hover:underline disabled:opacity-40">
          {testing === "telegram" ? "Sending…" : "Send test message"}
        </button>
        {statusBadge("telegram")}
      </div>
    </div>,

    // Step 3: Broker
    <div key="broker" className="flex flex-col gap-4">
      <p className="text-xs text-text-secondary">Choose trading mode. Paper mode requires no broker credentials.</p>
      {["paper", "shadow", "live"].map((mode) => (
        <button
          key={mode}
          onClick={() => set("TRADING_MODE")(mode)}
          className={clsx("text-xs px-3 py-2 rounded border text-left", vals.TRADING_MODE === mode ? "bg-blue/20 border-blue/40 text-blue" : "border-border text-text-muted hover:border-blue/30")}
        >
          <span className="font-semibold capitalize">{mode}</span>
          <span className="ml-2 text-text-muted">
            {mode === "paper" ? "— Simulated trades, no real money" : mode === "shadow" ? "— Signals only, no execution" : "— Real broker execution"}
          </span>
        </button>
      ))}
    </div>,

    // Step 4: Review
    <div key="review" className="flex flex-col gap-3">
      <p className="text-xs text-text-secondary">Review your configuration before saving.</p>
      {Object.entries(envStatus ?? {}).map(([k, v]) => (
        <div key={k} className="flex justify-between text-xs border-b border-border/30 py-1">
          <span className="text-text-muted mono">{k}</span>
          <span className={clsx("mono", v === "***set***" ? "text-green" : v ? "text-text-primary" : "text-text-muted")}>
            {v === "***set***" ? "✓ set" : v || "not set"}
          </span>
        </div>
      ))}
      {saved && <div className="text-green text-xs mt-2">✓ Configuration saved to .env</div>}
    </div>,
  ];

  return (
    <div className="flex h-full items-center justify-center bg-bg-primary">
      <div className="w-[520px] bg-bg-secondary border border-border rounded-lg overflow-hidden">
        {/* Progress */}
        <div className="flex border-b border-border">
          {STEPS.map((s, i) => (
            <button
              key={s}
              onClick={() => setStep(i)}
              className={clsx(
                "flex-1 py-2.5 text-xs font-semibold transition-colors",
                i === step ? "bg-blue/10 text-blue border-b-2 border-blue" : i < step ? "text-green" : "text-text-muted"
              )}
            >
              {i < step ? "✓ " : ""}{s}
            </button>
          ))}
        </div>

        {/* Content */}
        <div className="p-6 min-h-[280px]">
          <h2 className="text-sm font-bold text-text-primary mb-4">{STEPS[step]}</h2>
          {stepContent[step]}
        </div>

        {/* Navigation */}
        <div className="flex justify-between px-6 py-4 border-t border-border">
          <button
            onClick={() => setStep((s) => Math.max(0, s - 1))}
            disabled={step === 0}
            className="text-xs text-text-muted hover:text-text-primary disabled:opacity-30"
          >
            ← Back
          </button>
          {step < STEPS.length - 1 ? (
            <button
              onClick={() => setStep((s) => s + 1)}
              className="text-xs bg-blue text-bg-primary px-4 py-1.5 rounded font-semibold hover:bg-blue/80"
            >
              Next →
            </button>
          ) : (
            <button
              onClick={save}
              className="text-xs bg-green text-bg-primary px-4 py-1.5 rounded font-semibold hover:bg-green/80"
            >
              Save Configuration
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
