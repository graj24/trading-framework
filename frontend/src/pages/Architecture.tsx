import { useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";

// ── tiny primitives ────────────────────────────────────────────────────────────

function Badge({ children, color = "blue" }: { children: React.ReactNode; color?: string }) {
  const map: Record<string, string> = {
    blue:   "bg-blue-500/15 text-blue-300 border-blue-500/30",
    green:  "bg-green-500/15 text-green-300 border-green-500/30",
    yellow: "bg-yellow-500/15 text-yellow-300 border-yellow-500/30",
    red:    "bg-red-500/15 text-red-300 border-red-500/30",
    purple: "bg-purple-500/15 text-purple-300 border-purple-500/30",
    gray:   "bg-surface-700/50 text-content-muted border-surface-600",
  };
  return (
    <span className={`inline-block text-[10px] font-mono px-1.5 py-0.5 rounded border ${map[color] ?? map.blue}`}>
      {children}
    </span>
  );
}

function Tag({ children }: { children: React.ReactNode }) {
  return (
    <code className="text-[11px] bg-surface-800 text-accent-primary px-1.5 py-0.5 rounded font-mono">
      {children}
    </code>
  );
}

function Section({
  title, children, defaultOpen = true,
}: {
  title: string; children: React.ReactNode; defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="border border-surface-700 rounded-lg overflow-hidden mb-4">
      <button
        onClick={() => setOpen(!open)}
        className="w-full flex items-center gap-2 px-4 py-3 bg-surface-800 hover:bg-surface-750 text-left"
      >
        {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        <span className="text-sm font-semibold text-content-primary">{title}</span>
      </button>
      {open && <div className="px-4 py-4 bg-surface-900 space-y-3">{children}</div>}
    </div>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex gap-3 text-sm">
      <span className="w-44 shrink-0 text-content-muted text-xs pt-0.5">{label}</span>
      <div className="flex-1 text-content-secondary leading-relaxed">{children}</div>
    </div>
  );
}

function Table({ headers, rows }: { headers: string[]; rows: (React.ReactNode)[][] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs border-collapse">
        <thead>
          <tr className="border-b border-surface-700">
            {headers.map((h) => (
              <th key={h} className="text-left py-1.5 px-2 text-content-muted font-medium">{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={i} className="border-b border-surface-800 hover:bg-surface-800/40">
              {row.map((cell, j) => (
                <td key={j} className="py-1.5 px-2 text-content-secondary align-top">{cell}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ── sections ───────────────────────────────────────────────────────────────────

function OverviewSection() {
  return (
    <Section title="Overview">
      <p className="text-sm text-content-secondary leading-relaxed">
        A fully autonomous, competing multi-PM (Portfolio Manager) paper-trading framework for Indian
        equities (NSE). Two independent PM agents run 24/7, compete against each other, evolve their
        own strategies, and pick any NSE stock. All trading is paper-only — no real money moves.
      </p>
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mt-2">
        {[
          ["Exchange", "NSE (India)"],
          ["Mode", "Paper trading only"],
          ["PMs", "2 competing agents"],
          ["LLM", "Groq Llama-3.3-70b"],
        ].map(([k, v]) => (
          <div key={k} className="bg-surface-800 rounded p-3">
            <div className="text-[10px] text-content-muted uppercase tracking-wide">{k}</div>
            <div className="text-sm text-content-primary font-medium mt-0.5">{v}</div>
          </div>
        ))}
      </div>
    </Section>
  );
}

function DirectorySection() {
  const tree = [
    { path: "common/core/",       desc: "Shared infrastructure: event_bus, broker, config, pm_runtime, pm_state, pm_watchlist, holidays, timing, retry, concurrency, logger, costs, groww_client, watchlist, migrations" },
    { path: "common/agents/",     desc: "Base agent classes: BaseAgent, RiskManager, ExecutionAgent (canonical)" },
    { path: "common/universe/",   desc: "NSE universe loader — bhavcopy cache → BSE fallback, find_symbols(tier, sector)" },
    { path: "common/data_sources/",desc: "Pluggable DataSource ABC + built-ins: YFinanceSource, GrowwSource" },
    { path: "common/strategy/",   desc: "Versioned strategy YAML registry per PM + backtest gate (Sharpe gating)" },
    { path: "common/strategist/", desc: "Strategist brain: 7-step cycle, 5 actions, Groq LLM, journal + cycles.jsonl" },
    { path: "common/leaderboard/",desc: "Leaderboard snapshot: get_pm_stats, get_leaderboard, get_rival_snapshot" },
    { path: "core/",              desc: "Shims → common/core/ (backward compat). scheduler.py is the original file edited directly." },
    { path: "agents/",            desc: "Shims → common/agents/ or pm_1/agents/ (PM-specific overrides via spec_from_file_location)" },
    { path: "pm_1/agents/",       desc: "PM1's private agent implementations: master, technical, news, pattern, regime, intraday_scanner, sector_rotation, earnings_calendar, pre_open_monitor, data, learning, discovery" },
    { path: "pm_2/agents/",       desc: "PM2's private agents (stub — PM2 cold-starts and builds its own)" },
    { path: "pm_1/ pm_2/",        desc: "Per-PM workspaces: config.yaml, watchlist.yaml, strategies/, prompts/, state/ (gitignored), models/ (gitignored)" },
    { path: "api/routers/",       desc: "FastAPI routers: agents, backtest, candles, config, infra, market, pms, signals, trades, ws" },
    { path: "frontend/src/",      desc: "React + TypeScript UI: Terminal, Pipeline, PMs, Leaderboard, Backtest, Replay, Setup, Infra, Architecture" },
    { path: "tests/",             desc: "199 pytest tests (0 failures). Covers execution, broker, scheduler, strategist, signals, risk, prompt safety." },
  ];
  return (
    <Section title="Repository Layout">
      <Table
        headers={["Path", "Purpose"]}
        rows={tree.map(({ path, desc }) => [<Tag>{path}</Tag>, desc])}
      />
    </Section>
  );
}

function SchedulerSection() {
  const jobs = [
    ["06:00", "job_update_knowledge_bases", "Refresh OHLCV, fundamentals, sector data for all watchlist symbols"],
    ["07:00", "job_discover_stocks", "Scan news + volume + bulk deals to discover new NSE candidates"],
    ["08:30", "job_pre_market_analysis", "Run all PM agents (technical, news, pattern, regime, sector) per PM watchlist"],
    ["08:30 / 09:15 / 11:00 / 12:30 / 14:00 / 15:30", "job_pm_heartbeat", "Publish pm.wakeup.<pm_id> to event bus; optionally POST to Multica"],
    ["09:00", "job_preopen_scan", "PreOpenMonitor gap scan; ANOMALY alert if 0 results returned"],
    ["09:15", "job_execute_trades", "Run master agent per PM; place paper orders via ShadowBroker"],
    ["09:15–15:00 every 5 min", "job_monitor_positions", "Check SL/target; P&L limit alert at 75% of daily cap; news monitor on open positions"],
    ["09:15–15:00 every 5 min", "job_intraday_scan", "Intraday pattern scanner (IST-aware gate)"],
    ["15:00", "job_close_all_positions", "Force-close all open paper positions"],
    ["15:30", "job_post_market", "Daily report, learning update, KB write-back"],
    ["15:30", "job_earnings_evening_prep", "Prepare earnings calendar for next session"],
    ["15:45", "job_prune_watchlist", "Remove stale symbols from PM watchlists"],
    ["18:00–08:00 every 30 min", "job_earnings_overnight", "Overnight earnings monitoring"],
  ];
  return (
    <Section title="Scheduler (core/scheduler.py — IST)">
      <Table
        headers={["Time (IST)", "Job", "What it does"]}
        rows={jobs.map(([t, j, d]) => [
          <span className="text-yellow-300 font-mono text-[10px]">{t}</span>,
          <Tag>{j}</Tag>,
          d,
        ])}
      />
    </Section>
  );
}

function AgentsSection() {
  const agents = [
    ["master.py", "PM1", "Orchestrator: collects all scores → composite → LLM decision → hard filter gate → risk check → execute"],
    ["technical_agent.py", "PM1", "RSI, MACD, EMA50 trend, volume ratio, intraday 5m signals, VWAP"],
    ["news_agent.py", "PM1", "FinBERT sentiment on headlines; tier classification (1=critical, 2=high, 3=normal)"],
    ["pattern_agent.py", "PM1", "Candlestick pattern recognition; expected value (EV) and win-rate from KB history"],
    ["regime_agent.py", "PM1", "Market regime detection: trending_bull / trending_bear / ranging / high_volatility"],
    ["sector_rotation_agent.py", "PM1", "Sector momentum scoring; correlation matrix from KB"],
    ["intraday_scanner.py", "PM1", "5-min intraday ML signal + dynamic VIX-adjusted threshold"],
    ["earnings_calendar_agent.py", "PM1", "Earnings date awareness; morning scan for strong_buys/buys around earnings"],
    ["pre_open_monitor.py", "PM1", "NSE pre-open session gap-up/gap-down scanner"],
    ["data_agent.py", "PM1", "Fetches OHLCV + fundamentals; writes to knowledge base"],
    ["learning_agent.py", "PM1", "Post-trade learning: updates signal_weights.json in KB from closed trade outcomes"],
    ["discovery_agent.py", "PM1", "Discovers new NSE symbols via news volume + bulk deals"],
    ["ExecutionAgent", "common", "execute_trade: paper/live/shadow modes; persists all signal columns to SQLite; _get_ltp tries Groww first"],
    ["RiskManager", "common", "check_circuit_breaker: daily halt at -3%, weekly halve at -7%; Kelly position sizing"],
    ["pm_triage.py", "daemon", "Event bus listener: fast-path noise filter → cheap LLM escalation → routes to exec_order or pm.wakeup"],
    ["pm_trader.py", "daemon", "Listens exec_order.<pm_id>; deterministic pre-trade gates; places order via broker"],
    ["pm_risk.py", "daemon", "Continuous VaR + P&L monitor; publishes risk.breach.<pm_id> on limit hit"],
  ];
  return (
    <Section title="Agents">
      <Table
        headers={["Agent", "Owner", "Role"]}
        rows={agents.map(([a, o, r]) => [
          <Tag>{a}</Tag>,
          <Badge color={o === "PM1" ? "blue" : o === "common" ? "green" : "purple"}>{o}</Badge>,
          r,
        ])}
      />
    </Section>
  );
}

function StrategistSection() {
  return (
    <Section title="Strategist Brain (common/strategist/loop.py)">
      <p className="text-sm text-content-secondary">
        One <Tag>Strategist</Tag> process runs per PM (systemd <Tag>pm-strategist@{"<id>"}.service</Tag>).
        It wakes on <Tag>pm.wakeup.{"<pm_id>"}</Tag> events and on a 15-min off-shift cadence.
      </p>
      <Row label="7-step cycle">
        Read state → Read rivals → Drain inbox → Decide (LLM) → Execute action → Journal → Emit event
      </Row>
      <Row label="5 actions">
        <div className="flex flex-wrap gap-1.5">
          {[
            ["DO_NOTHING", "gray"],
            ["RESEARCH", "blue"],
            ["TRADE", "green"],
            ["EVOLVE", "yellow"],
            ["PIVOT", "red"],
          ].map(([a, c]) => <Badge key={a} color={c}>{a}</Badge>)}
        </div>
      </Row>
      <Row label="EVOLVE gate">
        Runs <Tag>backtest_strategy()</Tag> (GapStrategy proxy) on the proposed watchlist.
        New strategy only commits if new Sharpe ≥ current Sharpe.
      </Row>
      <Row label="LLM models">
        Heartbeat shifts → <Tag>groq/llama-3.3-70b-versatile</Tag> · Off-shift research → <Tag>groq/llama-3.1-8b-instant</Tag> · Fallback → <Tag>DO_NOTHING</Tag>
      </Row>
      <Row label="Outputs">
        <Tag>pm_{"<id>"}/state/journal.md</Tag> (append) · <Tag>pm_{"<id>"}/state/cycles.jsonl</Tag> · <Tag>agent.thinking.{"<pm_id>"}</Tag> event
      </Row>
    </Section>
  );
}

function EventBusSection() {
  const topics = [
    ["pm.wakeup.<pm_id>", "Scheduler heartbeat or triage escalation → wakes Strategist"],
    ["exec_order.<pm_id>", "Triage → PM Trader daemon to place an order"],
    ["research.<pm_id>", "Triage → PM Researcher queue"],
    ["risk.breach.<pm_id>", "PM Risk daemon → circuit breaker triggered"],
    ["system.pm.<pm_id>", "PM paused / resumed events"],
    ["system.daemon.<pm_id>", "Daemon start/stop lifecycle events"],
    ["agent.thinking.<pm_id>", "Strategist emits thinking status for UI live feed"],
    ["fill.<pm_id>", "Scheduler emits on SL/target exit"],
    ["news.<symbol>", "Scheduler emits on tier-1/2 news hit for open position"],
    ["price.spike.<symbol>", "Example custom event (extensible)"],
  ];
  return (
    <Section title="Event Bus (common/core/event_bus.py)">
      <p className="text-sm text-content-secondary mb-2">
        SQLite-backed pub/sub. All daemons subscribe; events are persisted and replayable.
        The UI streams events via <Tag>/ws/pm_events</Tag>.
      </p>
      <Table
        headers={["Topic", "Meaning"]}
        rows={topics.map(([t, d]) => [<Tag>{t}</Tag>, d])}
      />
    </Section>
  );
}

function DataSection() {
  return (
    <Section title="Data Layer">
      <Row label="Market data">
        <Tag>YFinanceSource</Tag> (default) · <Tag>GrowwSource</Tag> (LTP primary, yfinance fallback).
        Pluggable via <Tag>register_source()</Tag> / <Tag>get_source()</Tag>.
      </Row>
      <Row label="NSE universe">
        <Tag>common/universe/nse.py</Tag> — bhavcopy CSV cache (daily refresh) → BSE fallback.
        <Tag>find_symbols(tier=, sector=)</Tag> returns large/mid/small/sme caps.
      </Row>
      <Row label="Knowledge base">
        Per-symbol JSON files under <Tag>knowledge_base/{"<symbol>"}/</Tag>:
        {" "}fundamentals.json · news_history.json · sector_correlation.json · signal_weights.json (learned).
      </Row>
      <Row label="Trades DB">
        <Tag>paper_trades.db</Tag> (SQLite). Columns: symbol, action, qty, entry_price, exit_price,
        outcome, pnl_inr, pnl_pct, technical_score, sentiment, pattern_ev, sector_momentum,
        regime_alignment, weights_applied, signal_source, pm_id, timestamp.
      </Row>
      <Row label="Strategy registry">
        <Tag>pm_{"<id>"}/strategies/v001.yaml</Tag> … versioned YAML artefacts.
        <Tag>ACTIVE</Tag> file points to current version. <Tag>diff(pm_id, vA, vB)</Tag> for UI diff view.
      </Row>
    </Section>
  );
}

function BrokerSection() {
  return (
    <Section title="Broker & Execution">
      <Row label="ShadowBroker">
        Wraps a <Tag>PaperBroker</Tag> + optional live broker. In paper mode, fills are simulated
        immediately at LTP. In live mode, routes to Zerodha / Upstox / AngelOne.
      </Row>
      <Row label="Paper fills">
        <Tag>PaperBroker.place_order()</Tag> → instant fill at current price, logged to
        {" "}<Tag>paper_trades.db</Tag>. Circuit breaker: 5 consecutive losses → halt.
      </Row>
      <Row label="Rate limits">
        Global: 30 orders/min across all PMs. Per-PM: 10 orders/min.
        {" "}<Tag>_reset_rate_limiters()</Tag> available for test isolation.
      </Row>
      <Row label="Kill switch">
        <Tag>/api/pms/kill_switch/activate</Tag> halts all order placement immediately.
        Persisted in <Tag>pm_state</Tag>; survives restarts.
      </Row>
    </Section>
  );
}

function RiskSection() {
  return (
    <Section title="Risk Management">
      <Table
        headers={["Rule", "Threshold", "Action"]}
        rows={[
          ["Daily loss limit",   "-3% of capital",  "Halt all trading for the day"],
          ["Weekly loss limit",  "-7% of capital",  "Halve position sizes for the week"],
          ["Monthly loss limit", "-15% of capital", "Full halt, alert"],
          ["Per-trade loss",     "-1% of capital",  "Stop-loss gate before entry"],
          ["Max open positions", "3 simultaneous",  "Block new entries"],
          ["P&L proximity alert","75% of daily limit","Telegram alert fired"],
          ["Trailing stop",      "0.5% distance, triggers at +1%", "Auto-trail stop-loss"],
        ]}
      />
      <Row label="Position sizing">
        Half-Kelly formula: <Tag>kelly_size(win_rate, avg_win_pct, avg_loss_pct, capital, fraction=0.5)</Tag>.
        Falls back to 10% of capital if no trade history.
      </Row>
    </Section>
  );
}

function APISection() {
  const endpoints = [
    ["GET",  "/api/pms",                        "List all registered PMs"],
    ["GET",  "/api/pms/leaderboard?window_days=30", "Ranked leaderboard with Sharpe, win rate, P&L"],
    ["GET",  "/api/pms/{id}/state",             "PM state: positions, journal, plan"],
    ["GET",  "/api/pms/{id}/rivals",            "Rival PM snapshot for comparison"],
    ["GET",  "/api/pms/{id}/strategies",        "List strategy versions"],
    ["GET",  "/api/pms/{id}/strategies/diff",   "Diff two strategy versions"],
    ["POST", "/api/pms/{id}/pause",             "Pause a PM"],
    ["POST", "/api/pms/{id}/resume",            "Resume a PM"],
    ["GET",  "/api/pms/kill_switch",            "Kill switch status"],
    ["POST", "/api/pms/kill_switch/activate",   "Activate kill switch"],
    ["GET",  "/api/trades",                     "Trade history with filters"],
    ["GET",  "/api/signals",                    "Latest signals per symbol"],
    ["GET",  "/api/market",                     "Market status, indices"],
    ["GET",  "/api/backtest",                   "Run backtest on strategy"],
    ["WS",   "/ws/live",                        "Live event stream (all events)"],
    ["WS",   "/ws/pm_events?pm_id=1",           "Per-PM event stream"],
    ["WS",   "/ws/journal/{pm_id}",             "Live journal tail for a PM"],
    ["WS",   "/ws/leaderboard",                 "Leaderboard push every 30s"],
  ];
  return (
    <Section title="API (FastAPI)">
      <Table
        headers={["Method", "Endpoint", "Purpose"]}
        rows={endpoints.map(([m, e, p]) => [
          <Badge color={m === "WS" ? "purple" : m === "POST" ? "yellow" : "blue"}>{m}</Badge>,
          <Tag>{e}</Tag>,
          p,
        ])}
      />
    </Section>
  );
}

function PM2Section() {
  return (
    <Section title="PM2 Cold-Start & Competition">
      <p className="text-sm text-content-secondary">
        PM2 starts blank. On its first Strategist wakeup it reads PM1's strategy and trade history,
        then picks one of four cold-start paths:
      </p>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-2 mt-2">
        {[
          ["A — Blank", "Build from scratch, ignore PM1 entirely"],
          ["B — Inherit", "Clone PM1's active strategy, then diverge"],
          ["C — Research first", "Spend first cycles scanning NSE universe before committing"],
          ["D — Counter-strategy", "Identify PM1's weakest signals and exploit the gaps"],
        ].map(([title, desc]) => (
          <div key={title} className="bg-surface-800 rounded p-3">
            <div className="text-xs font-semibold text-accent-primary">{title}</div>
            <div className="text-xs text-content-muted mt-0.5">{desc}</div>
          </div>
        ))}
      </div>
      <Row label="Competition">
        Both PMs share the same <Tag>paper_trades.db</Tag> (rows tagged by <Tag>pm_id</Tag>).
        Leaderboard ranks by Sharpe ratio over a rolling window (7D / 30D / 90D).
        PMs can take opposing positions on the same stock.
      </Row>
      <Row label="Strategy evolution">
        Each EVOLVE cycle runs a backtest gate. New strategy version only activates if
        Sharpe improves. All versions are stored in <Tag>pm_{"<id>"}/strategies/</Tag> and diffable in the UI.
      </Row>
    </Section>
  );
}

function PromptSafetySection() {
  return (
    <Section title="Prompt Safety" defaultOpen={false}>
      <Row label="Untrusted headlines">
        News headlines are injected into a separate <Tag>user</Tag> message wrapped in
        {" "}<Tag>{"<untrusted-headlines>"}</Tag> tags, never in the main prompt string.
        Each headline is truncated to 160 chars.
      </Row>
      <Row label="System framing">
        A <Tag>system</Tag> message instructs the LLM to treat the headlines block as raw data only
        and not follow any embedded instructions.
      </Row>
      <Row label="Volume gate">
        Missing <Tag>volume_ratio</Tag> (indicator failure) is treated as a hard block on BUY —
        fail-closed, not fail-open.
      </Row>
    </Section>
  );
}

// ── page export ────────────────────────────────────────────────────────────────

export function Architecture() {
  return (
    <div className="flex-1 overflow-y-auto p-6 max-w-5xl mx-auto w-full">
      <div className="mb-6">
        <h1 className="text-xl font-bold text-content-primary">Architecture</h1>
        <p className="text-sm text-content-muted mt-1">
          End-to-end technical reference for the competing multi-PM trading framework.
        </p>
      </div>
      <OverviewSection />
      <DirectorySection />
      <SchedulerSection />
      <AgentsSection />
      <StrategistSection />
      <EventBusSection />
      <DataSection />
      <BrokerSection />
      <RiskSection />
      <APISection />
      <PM2Section />
      <PromptSafetySection />
    </div>
  );
}
