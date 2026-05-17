# 01 — Architecture (Internal Analysis)

> Audience: you (the developer). Honest, opinionated, grounded in the code.
> Companion: see `02-data-flow.md` for diagrams, `03-agents.md` for per-agent detail.

---

## 1. What this codebase actually is

A **single-process, agent-style equity trading framework** for Indian (NSE) stocks. Despite the "agent" naming, it is **not** an LLM-tool-calling agent loop — agents are plain Python classes that subclass `Agent` (`agents/base.py`) and return `AgentResult` objects. Orchestration is hard-coded in `MasterAgent.run_for_stock()` (`agents/master.py`).

Two things genuinely use ML/LLMs:

1. The **final trade decision** in `MasterAgent` calls a chat LLM (Groq `llama-3.3-70b-versatile` via `litellm`). It falls back to a deterministic rule-based scorer when the LLM call fails.
2. Two **gradient-boosting classifiers** generate auxiliary scores: `ml_model.py` (daily, 5-day horizon) and `india_intraday_model.py` (1-hour, 3-hour horizon).

Everything else — data, news, technical, pattern, regime, risk, execution, learning — is rule-based / statistical.

## 2. Layered view

```
┌────────────────────────────────────────────────────────────────────┐
│  ENTRY POINTS                                                       │
│  main.py                  — single cycle (run watchlist once)       │
│  core/scheduler.py        — APScheduler 24/7 daemon                 │
│  test_stock.py            — full single-stock demo                  │
│  simulate_day.py          — time-travel a historical big-move day   │
│  dashboard.py             — Streamlit UI                            │
│  backtest_*.py            — strategy backtests                      │
└────────────────────────────────────────────────────────────────────┘
                                │
┌────────────────────────────────────────────────────────────────────┐
│  ORCHESTRATION                                                      │
│  agents/master.py         — MasterAgent (synchronous orchestrator)  │
│  core/scheduler.py        — cron + interval jobs (Asia/Kolkata)     │
└────────────────────────────────────────────────────────────────────┘
                                │
┌────────────────────────────────────────────────────────────────────┐
│  AGENTS  (all extend agents/base.py:Agent → return AgentResult)     │
│                                                                     │
│  Decision-side                  Discovery / monitoring              │
│  ──────────────                 ──────────────────────              │
│  data_agent.py                  discovery_agent.py                  │
│  news_agent.py                  pre_open_monitor.py                 │
│  technical_agent.py             intraday_scanner.py                 │
│  pattern_agent.py               earnings_calendar_agent.py          │
│  regime_agent.py                                                    │
│  risk_manager.py                Outcome-side                        │
│  execution_agent.py             ─────────────                       │
│  learning_agent.py              (execution + learning above)        │
└────────────────────────────────────────────────────────────────────┘
                                │
┌────────────────────────────────────────────────────────────────────┐
│  ML MODELS                  CORE SERVICES                           │
│  ml_model.py (daily)        core/broker.py     (Paper / Zerodha)    │
│  india_intraday_model.py    core/groww_client.py (live LTP/quotes)  │
│  ripple/sentiment_analyzer  core/knowledge_base.py (per-stock JSON) │
│    (FinBERT, ProsusAI)      core/alerts.py     (Telegram)           │
│                             core/logger.py     (rotating file log)  │
│                             core/backtester.py (event-driven)       │
└────────────────────────────────────────────────────────────────────┘
                                │
┌────────────────────────────────────────────────────────────────────┐
│  STORAGE                                                            │
│  stocks/<SYM>/              — per-stock JSON KB + parquet history   │
│  stocks_1h/                 — 1h candles + intraday model.pkl       │
│  paper_trades.db            — SQLite trade ledger                   │
│  config.yaml                — runtime config (watchlist, risk, …)   │
│  .env                       — secrets (Groww, Twitter, Telegram, …) │
│  logs/                      — rotating log files                    │
└────────────────────────────────────────────────────────────────────┘
```

## 3. The graph view (community structure)

The `graphify-out/GRAPH_REPORT.md` confirms what reading the code suggests:

| God node       | Edges | Role                                                     |
|----------------|-------|----------------------------------------------------------|
| `run()`        | 49    | Generic agent entry-point — most agents implement it     |
| `AgentResult`  | 25    | Universal return contract                                |
| `MasterAgent`  | 24    | Cross-community bridge (orchestrator)                    |
| `Agent`        | 21    | Abstract base                                            |
| `_load_config` | 20    | Config is read from many places (a smell — see issues)   |
| `DataAgent`    | 20    | Owns the per-stock KB                                    |
| `PaperBroker`  | 17    | Live execution path                                      |
| `ExecutionAgent` | 16  | Trade ledger                                             |

Communities are essentially: **data**, **execution**, **broker/groww**, **base/agents**, **discovery**, **ripple**, **backtester**, **master/decision**, **risk**, **patterns/intraday**, **news**, **ML model**, **simulation/test**.

## 4. Control flow at a glance

There are **three** valid ways to drive the system, all use the same agents underneath:

### 4a. `python main.py` — single cycle
- For every symbol in `config.yaml:watchlist`:
  - `MasterAgent.run_for_stock(symbol)` → BUY / HOLD / SKIP
  - If `BUY` and confidence ≥ 60 and no open position: `ExecutionAgent.execute_trade(...)`
- Then it walks all open positions and prints unrealised P&L; reads closed trades and feeds their outcomes to `LearningAgent.update_weights()`.

### 4b. `python main.py --schedule` — scheduler daemon
APScheduler with `Asia/Kolkata` timezone. Cron + interval jobs (full schedule in `02-data-flow.md`):

| Time (IST)        | Job                        | Calls                                                      |
|-------------------|----------------------------|------------------------------------------------------------|
| 06:00             | `job_update_knowledge_bases` | `DataAgent.build_kb()` for full watchlist                |
| 07:00             | `job_discover_stocks`       | `DiscoveryAgent.discover()` — adds top picks to watchlist |
| 08:30             | `job_pre_market_analysis`   | Tech + regime preview                                      |
| 09:00             | `job_preopen_scan`          | `PreOpenMonitor.scan()` + earnings morning scan           |
| 09:00             | `job_generate_signals`      | `MasterAgent.run_for_stock()` for each watchlist symbol    |
| 09:15             | `job_execute_trades`        | Master + ExecutionAgent + Telegram                         |
| every 5 min       | `job_monitor_positions`     | SL/target check + news monitor                             |
| every 5 min       | `job_intraday_scan`         | `IntradayPatternScanner.scan_all()`                        |
| 15:00             | `job_close_all_positions`   | `ExecutionAgent.emergency_exit()` for every open trade     |
| 15:30             | `job_post_market`           | Daily report + per-stock weekly analysis                   |
| 15:30             | `job_earnings_evening_prep` | `EarningsCalendarAgent.evening_prep()`                     |
| 15:45             | `job_prune_watchlist`       | Trim watchlist to `watchlist_max` (=20)                    |
| 18:00–08:00, /30m | `job_earnings_overnight`    | NSE/BSE filings monitor                                    |

### 4c. `streamlit run dashboard.py` — read-only UI
Five tabs: Portfolio, Signals, Backtest (gap strategy), News, Intraday ML. Reads `paper_trades.db` and per-stock KB; **does not** execute trades.

## 5. The decision pipeline (high-level)

(Full step-by-step in `04-decision-pipeline.md`.)

```
            ┌──────────────────────────────────────────────────────┐
            │  MasterAgent.run_for_stock(symbol)                   │
            └──────────────────────────────────────────────────────┘
                              │
   ┌──────────┬──────────┬────┴───────┬────────────┬─────────────┐
   ▼          ▼          ▼            ▼            ▼             ▼
TechAgent  NewsAgent  PatternAgent  RegimeAgent   ml_model    intraday
                                                  (daily)     model (1h)
   │          │          │            │            │             │
   └──────────┴────┬─────┴────────────┘            │             │
                   ▼                               │             │
        scores dict (technical, RSI, MACD,         │             │
        intraday_score, sentiment, tier,           │             │
        pattern_ev, win_rate, regime, ...)         │             │
                   │                               │             │
                   ▼                               ▼             ▼
          Tier-1 emergency skip?           ml_proba       intraday_proba
                   │                                            │
                   ▼                              + dynamic_threshold(VIX, regime, hour, FO)
          _rag_context(symbol) — read fundamentals, event_reactions,
                                 sector_correlation, signal_weights, patterns
                   │
                   ▼
          _llm_decision(symbol, price, scores, rag, config)
                   │     ├─ litellm.completion(groq/llama-3.3-70b-versatile, prompt)
                   │     └─ on exception → _rule_based_decision(price, scores)
                   ▼
          {decision, confidence, entry, stop_loss, target, reasoning}
                   │
                   ▼
          confidence < 60 and decision == BUY  →  HOLD
          BUY but trend != up / MACD != bullish / vol < 1×  →  HOLD
                   │
                   ▼
          decision == BUY → RiskManager.run({symbol, entry, win_rate, …})
                            ├─ Kelly half-fraction sizing
                            ├─ ATR-based SL (overrides LLM SL if missing)
                            ├─ correlation gate (>0.8 with open pos)
                            ├─ sector overlap gate (≤2 per sector)
                            └─ daily/weekly/monthly loss limits
                   ▼
          AgentResult({symbol, decision, confidence, entry_price,
                       stop_loss, target, position_size, reasoning, agent_scores})
```

## 6. Storage model

### 6a. Per-stock knowledge base — `stocks/<SYMBOL>/`
Files (created by `core/knowledge_base.py:init_kb`):

| File                       | Owner agent             | Purpose                                       |
|----------------------------|-------------------------|-----------------------------------------------|
| `price_history.parquet`    | DataAgent               | Daily OHLCV (5y default)                      |
| `fundamentals.json`        | DataAgent               | PE, EPS, market cap, sector, 52w high/low …   |
| `earnings_history.json`    | DataAgent + EarningsCal | Quarterly results + price reaction            |
| `corporate_actions.json`   | DataAgent               | Dividends, splits                             |
| `sector_correlation.json`  | DataAgent               | Correlation with Nifty + 8 sector indices     |
| `event_reactions.json`     | DataAgent + EarningsCal | Avg reaction per event type, premarket signals |
| `signal_weights.json`      | LearningAgent           | Per-stock signal weight EMA (default 1.0)     |
| `news_history.json`        | NewsAgent               | Last 500 headlines + tier + sentiment         |
| `patterns.json`            | PatternAgent            | Top-5 DTW matches + EV summary                |
| `bulk_deals.json`          | DiscoveryAgent (TBD)    | Stub — currently empty `{}` for all stocks    |

### 6b. 1h dataset — `stocks_1h/`
Per-symbol parquet of hourly candles + market context (`NIFTY_1h.parquet`, `BANKNIFTY_1h.parquet`, `VIX_1h.parquet`) + the trained `india_intraday_model.pkl`. Driven entirely by `india_intraday_model.py fetch|train|predict`.

### 6c. Trade ledger — `paper_trades.db` (SQLite)
Single table `trades`, schema in `agents/execution_agent.py:_get_conn`. Columns: `id, symbol, entry_date, entry_price, stop_loss, target, position_size, exit_date, exit_price, pnl_pct, pnl_inr, outcome, reasoning, created_at`.

### 6d. ML artefacts
- `stocks/ml_signal_model.pkl` (daily, GradientBoosting, ~30 features)
- `stocks_1h/india_intraday_model.pkl` (1h, GradientBoosting, ~30 features incl. F&O expiry days)

## 7. External dependencies (network)

| Service              | Used in                                    | Free? | Notes                                                                  |
|----------------------|--------------------------------------------|-------|------------------------------------------------------------------------|
| yfinance             | DataAgent, NewsAgent, RegimeAgent, ml_model | Yes  | Primary price + news source. Frequently rate-limited.                  |
| NSE India website    | Discovery, PreOpen, EarningsCal, IntradayScanner | Yes | Cookies-then-API pattern; their APIs change without notice.        |
| BSE India            | EarningsCalendarAgent                      | Yes   | Used as backup for results filings.                                    |
| MoneyControl         | DiscoveryAgent (`fetch_moneycontrol_active`) | Yes  | HTML scrape of `bsr_table` rows.                                       |
| Nitter (Twitter mirror) | DiscoveryAgent                          | Yes   | Three instances tried in order; all unstable.                          |
| Google Trends (pytrends) | DiscoveryAgent                         | Yes   | Optional import.                                                       |
| Groww REST API       | core/groww_client.py + IntradayScanner     | Auth  | Real keys live in `.env` — used for batch LTP/quote/OHLC.              |
| Reddit (Pushshift-less) | ripple/twitter_collector.py             | Yes   | JSON search across stocks/wallstreetbets/investing subs.               |
| HuggingFace ProsusAI/finbert | ripple/sentiment_analyzer.py       | Yes   | Downloaded on first use.                                               |
| HuggingFace facebook/bart-large-cnn | ripple/sentiment_analyzer.py | Yes   | Used to summarise long news before sentiment scoring.                  |
| Groq via litellm     | MasterAgent (`_llm_decision`)              | Auth  | Single LLM call per stock per analysis cycle.                          |
| Telegram Bot API     | core/alerts.py                             | Auth  | Disabled when `TELEGRAM_BOT_TOKEN` not set.                            |
| Zerodha Kite         | core/broker.py:ZerodhaBroker               | Auth  | Only triggered if `trading.mode = live`. Currently `paper`.            |

## 8. Key design choices — opinion

| Choice                                                               | My take                                                                                                                                                |
|----------------------------------------------------------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------|
| Synchronous, sequential agents                                       | Fine for ~50 NIFTY stocks. Won't scale to thousands without async or concurrent.futures.                                                              |
| LLM as final arbiter with rule-based fallback                        | Pragmatic: LLM hallucinations are bounded by hard filters (`trend == "up"`, `MACD == bullish`, `vol >= 1×`) and confidence floor (60).                |
| Filesystem-as-database (per-stock JSON + parquet)                    | Excellent for inspection and debugging; fragile under concurrency (no locking). Fine for single-process scheduler.                                     |
| ML model is **advisory** — feeds into the LLM prompt, not gate       | Smart. Avoids over-fitting risk while keeping the predictive signal.                                                                                  |
| Two ML models (daily + 1h) with **different label thresholds**       | 1.5% / 5d for daily, 1.0% / 3h for intraday. Reasonable, but they're never compared side-by-side; you can't tell which one is actually carrying alpha. |
| Watchlist self-modification (`DiscoveryAgent` writes `config.yaml`)  | Quick + dirty. Loses comments and ordering on every save (`yaml.dump`).                                                                                |
| Hard-coded constants spread across modules                           | E.g. slippage = 0.0005 in `execution_agent.py` and 0.001 in `backtest_intraday.py`. See `05-issues.md`.                                               |
| `core/knowledge_base.py` global singleton path                       | Simple. Makes test fixtures awkward — there's no test suite anyway.                                                                                    |

## 9. What's missing (architecture-level)

- **No tests.** Not a single `tests/` directory.
- **No CI.** No `.github/`, no pre-commit, no linting config.
- **No type checking.** Type hints are present but inconsistent.
- **No metrics/observability.** Logs go to one file; no structured events, no Prometheus, no per-agent timing.
- **No retry/backoff.** Most network calls are bare `requests.get(..., timeout=8)`.
- **No transaction boundary on the trade ledger.** A crash mid-write could leak open positions.
- **No deduplication contract for ML predictions.** `predict()` recomputes market data for the full date range every call.

These show up as concrete tickets in `06-improvements.md`.
