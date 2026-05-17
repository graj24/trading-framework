import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, LeaderboardEntry, StrategyVersion } from "@/lib/api";

// ── Leaderboard table ─────────────────────────────────────────────────────────

function LeaderboardTable({ entries }: { entries: LeaderboardEntry[] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm font-mono">
        <thead>
          <tr className="text-text-muted border-b border-border-subtle text-left">
            <th className="py-2 pr-4">#</th>
            <th className="py-2 pr-4">PM</th>
            <th className="py-2 pr-4 text-right">P&L (₹)</th>
            <th className="py-2 pr-4 text-right">Trades</th>
            <th className="py-2 pr-4 text-right">Win %</th>
            <th className="py-2 pr-4 text-right">Sharpe</th>
            <th className="py-2 pr-4 text-right">Max DD (₹)</th>
            <th className="py-2 text-right">Open</th>
          </tr>
        </thead>
        <tbody>
          {entries.map((e, i) => (
            <tr key={e.pm_id} className="border-b border-border-subtle hover:bg-bg-secondary transition-colors">
              <td className="py-2 pr-4 text-text-muted">{i + 1}</td>
              <td className="py-2 pr-4 font-bold text-accent-blue">PM{e.pm_id}</td>
              <td className={`py-2 pr-4 text-right font-bold ${e.total_pnl >= 0 ? "text-accent-green" : "text-accent-red"}`}>
                {e.total_pnl >= 0 ? "+" : ""}₹{e.total_pnl.toLocaleString("en-IN", { maximumFractionDigits: 0 })}
              </td>
              <td className="py-2 pr-4 text-right text-text-secondary">{e.n_trades}</td>
              <td className="py-2 pr-4 text-right text-text-secondary">{e.win_rate_pct.toFixed(1)}%</td>
              <td className={`py-2 pr-4 text-right ${e.sharpe >= 1 ? "text-accent-green" : e.sharpe >= 0 ? "text-text-secondary" : "text-accent-red"}`}>
                {e.sharpe.toFixed(2)}
              </td>
              <td className="py-2 pr-4 text-right text-accent-red">
                {e.max_drawdown_inr > 0 ? `-₹${e.max_drawdown_inr.toLocaleString("en-IN", { maximumFractionDigits: 0 })}` : "—"}
              </td>
              <td className="py-2 text-right text-text-secondary">{e.open_positions}</td>
            </tr>
          ))}
          {entries.length === 0 && (
            <tr><td colSpan={8} className="py-8 text-center text-text-muted">No trade history yet</td></tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

// ── Journal stream ────────────────────────────────────────────────────────────

function JournalStream({ pmId }: { pmId: string }) {
  const [lines, setLines] = useState<string>("");
  const bottomRef = useRef<HTMLDivElement>(null);
  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    const base = import.meta.env.VITE_WS_URL
      ? import.meta.env.VITE_WS_URL.replace(/\/ws\/live$/, "")
      : `${proto}//${window.location.host}`;
    const ws = new WebSocket(`${base}/ws/journal/${pmId}`);
    wsRef.current = ws;

    ws.onmessage = (e) => {
      const msg = JSON.parse(e.data);
      if (msg.type === "journal_init") setLines(msg.content);
      else if (msg.type === "journal_append") setLines((prev) => prev + msg.content);
    };
    ws.onclose = () => {};

    const ping = setInterval(() => {
      if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: "ping" }));
    }, 20000);

    return () => { clearInterval(ping); ws.close(); };
  }, [pmId]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [lines]);

  return (
    <div className="h-64 overflow-y-auto bg-bg-secondary rounded border border-border-subtle p-3 font-mono text-xs text-text-secondary whitespace-pre-wrap">
      {lines || <span className="text-text-muted">Waiting for journal entries…</span>}
      <div ref={bottomRef} />
    </div>
  );
}

// ── Strategy version history ──────────────────────────────────────────────────

function StrategyHistory({ pmId }: { pmId: string }) {
  const { data } = useQuery({
    queryKey: ["strategies", pmId],
    queryFn: () => api.pmStrategies(pmId),
    refetchInterval: 60_000,
  });
  const [diffPair, setDiffPair] = useState<[number, number] | null>(null);
  const [diffText, setDiffText] = useState<string>("");

  const versions: StrategyVersion[] = data?.versions ?? [];
  const activeVer = data?.active_version;

  const showDiff = async (vA: number, vB: number) => {
    setDiffPair([vA, vB]);
    const res = await api.pmStrategyDiff(pmId, vA, vB);
    setDiffText(res.diff);
  };

  return (
    <div className="space-y-2">
      <div className="text-xs text-text-muted mb-2">
        Active: <span className="text-accent-blue font-bold">v{activeVer ?? "—"}</span>
        {" · "}{versions.length} version{versions.length !== 1 ? "s" : ""}
      </div>
      <div className="space-y-1 max-h-40 overflow-y-auto">
        {versions.map((v) => (
          <div key={v.version} className="flex items-start gap-2 text-xs font-mono">
            <span className={`shrink-0 ${v.version === activeVer ? "text-accent-green font-bold" : "text-text-muted"}`}>
              v{String(v.version).padStart(3, "0")}
            </span>
            <span className="text-text-secondary flex-1 truncate" title={v.notes}>{v.notes || "(no notes)"}</span>
            {v.parent_version != null && (
              <button
                className="shrink-0 text-accent-blue hover:underline"
                onClick={() => showDiff(v.parent_version!, v.version)}
              >
                diff
              </button>
            )}
          </div>
        ))}
        {versions.length === 0 && <div className="text-text-muted">No versions yet</div>}
      </div>
      {diffPair && diffText && (
        <div className="mt-2">
          <div className="text-xs text-text-muted mb-1">
            Diff v{diffPair[0]} → v{diffPair[1]}
            <button className="ml-2 text-accent-red" onClick={() => { setDiffPair(null); setDiffText(""); }}>✕</button>
          </div>
          <pre className="text-xs bg-bg-secondary rounded border border-border-subtle p-2 overflow-x-auto max-h-48 overflow-y-auto whitespace-pre">
            {diffText.split("\n").map((line, i) => (
              <span key={i} className={
                line.startsWith("+") ? "text-accent-green" :
                line.startsWith("-") ? "text-accent-red" :
                line.startsWith("@@") ? "text-accent-blue" : "text-text-secondary"
              }>{line}{"\n"}</span>
            ))}
          </pre>
        </div>
      )}
    </div>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

export function Leaderboard() {
  const [windowDays, setWindowDays] = useState(30);
  const [selectedPm, setSelectedPm] = useState<string | null>(null);

  const { data: board = [], isLoading } = useQuery({
    queryKey: ["leaderboard", windowDays],
    queryFn: () => api.leaderboard(windowDays),
    refetchInterval: 30_000,
  });

  // Live leaderboard via WebSocket
  const [liveBoard, setLiveBoard] = useState<LeaderboardEntry[] | null>(null);
  useEffect(() => {
    const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    const base = import.meta.env.VITE_WS_URL
      ? import.meta.env.VITE_WS_URL.replace(/\/ws\/live$/, "")
      : `${proto}//${window.location.host}`;
    const ws = new WebSocket(`${base}/ws/leaderboard`);
    ws.onmessage = (e) => {
      const msg = JSON.parse(e.data);
      if (msg.type === "leaderboard") setLiveBoard(msg.data);
    };
    return () => ws.close();
  }, []);

  const entries = liveBoard ?? board;
  const pmIds = entries.map((e) => e.pm_id);

  return (
    <div className="h-full overflow-y-auto p-4 space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-bold text-text-primary font-mono">LEADERBOARD</h1>
        <div className="flex gap-2">
          {[7, 30, 90].map((d) => (
            <button
              key={d}
              onClick={() => setWindowDays(d)}
              className={`px-3 py-1 text-xs font-mono rounded border transition-colors ${
                windowDays === d
                  ? "border-accent-blue text-accent-blue bg-accent-blue/10"
                  : "border-border-subtle text-text-muted hover:border-text-muted"
              }`}
            >
              {d}D
            </button>
          ))}
        </div>
      </div>

      {/* Rankings */}
      <div className="bg-bg-secondary rounded border border-border-subtle p-4">
        {isLoading ? (
          <div className="text-text-muted text-sm font-mono">Loading…</div>
        ) : (
          <LeaderboardTable entries={entries} />
        )}
      </div>

      {/* Per-PM panels */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {pmIds.map((pmId) => (
          <div key={pmId} className="bg-bg-secondary rounded border border-border-subtle p-4 space-y-4">
            <div className="flex items-center justify-between">
              <h2 className="font-mono font-bold text-accent-blue">PM{pmId}</h2>
              <button
                onClick={() => setSelectedPm(selectedPm === pmId ? null : pmId)}
                className="text-xs text-text-muted hover:text-text-primary font-mono"
              >
                {selectedPm === pmId ? "hide journal" : "show journal"}
              </button>
            </div>

            {/* Strategy history */}
            <div>
              <div className="text-xs text-text-muted font-mono mb-2 uppercase tracking-wider">Strategy History</div>
              <StrategyHistory pmId={pmId} />
            </div>

            {/* Journal stream (toggle) */}
            {selectedPm === pmId && (
              <div>
                <div className="text-xs text-text-muted font-mono mb-2 uppercase tracking-wider">Live Journal</div>
                <JournalStream pmId={pmId} />
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
