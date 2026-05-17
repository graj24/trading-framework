# 05 — Issues (Internal Analysis)

> Bugs, smells, and concrete risks. Severity is my best estimate — feel free to re-prioritise.
> Each entry: **What → Where → Why it matters → Suggested fix**.

Severity legend: **🔴 critical** · **🟠 high** · **🟡 medium** · **🟢 low**

---

## A. Security

### A1. 🔴 Live API credentials live in `.env` on disk
**Where**: `/Users/gaurav/litellm-bedrock/trading-framework/.env`.
The file contains `GROWW_ACCESS_TOKEN`, `GROWW_API_KEY`, `GROWW_SECRET`, `GROWW_TOTP_SECRET`, plus Twitter API keys (`TWITTER_BEARER_TOKEN`, `TWITTER_API_KEY`, `TWITTER_API_SECRET`, `TWITTER_ACCESS_TOKEN`, `TWITTER_ACCESS_TOKEN_SECRET`). The Groww access token is the **JWT used for actual order placement and account access** for the live brokerage account.

**Why it matters**: anyone with read access to this directory (cloud sync, backups, time-machine, accidental `tar`) gets full broker access. Even though `.env` is in `.gitignore`, it's still on disk.

**Fix**: rotate every credential listed above today. Move secrets to a real secret store (macOS Keychain, 1Password CLI, AWS Secrets Manager), and have the loader read from there. Stop committing real-keys-shaped strings even to `.env.example`.

### A2. 🟠 No input validation on symbol → file path
**Where**: `core/knowledge_base.py:kb_path()` returns `STOCKS_DIR / symbol.upper()`.
**Why**: a malicious symbol like `../../etc` would resolve outside `stocks/`. Today symbols come from config and yfinance, but `DiscoveryAgent` writes user-influenced symbols back into `config.yaml`. Path traversal is a latent risk.
**Fix**: validate `symbol` matches `^[A-Z0-9_&\-]{1,15}$` before any FS write.

### A3. 🟡 `requests` calls without TLS verification or timeout in places
**Where**: most `requests.get` calls have a timeout, but several pre-token Groww POSTs silently fall back to `Authorization: Bearer <api_key>` (`core/groww_client.py:_ensure_token`) without verifying that token-fetch actually failed for the right reason.
**Fix**: distinguish HTTP errors from JSON-parse errors; refuse to proceed with broken auth.

### A4. ✅ RESOLVED — LLM prompt-injection mitigations
**Fixed in**: `fix/verification-findings` (MED-8).
**What landed**: `_llm_decision` now sends a system message explicitly framing headlines as untrusted, plus a separate user message containing the headlines inside a `<untrusted-headlines>` block, with each headline truncated to 160 characters. 3 unit tests cover the structure.

---

## B. Correctness bugs

### B0. ✅ RESOLVED — `main.py` no longer crashes on closed trades
**Fixed in**: `fix/verification-findings` (CRIT-1).
**What landed**: new helper `core/row_utils.py:row_get(row, key, default)` that handles `sqlite3.Row`, dicts, and `None` uniformly. `main.py:135-138` now uses it. 6 unit tests in `tests/test_crit1_row_get.py`.

### B0b. ✅ RESOLVED — `trades` schema now stores entry-time signals
**Fixed in**: `fix/verification-findings` (CRIT-2).
**What landed**: idempotent `migrate_trades_schema()` in `agents/execution_agent.py` adds `technical_score / sentiment / pattern_ev / sector_momentum / regime_alignment / weights_applied`. `ExecutionAgent.execute_trade(...)` accepts a new `signals_at_entry` dict. `main.py` and `core/scheduler.py` pass it through on every BUY. The DB has been migrated in-place; the existing closed trade is preserved.

### B0c. ✅ RESOLVED — `CANDLE_LOOKBACK` / `CANDLE_INTERVAL` defined
**Fixed in**: `fix/verification-findings` (HIGH-3).
**What landed**: `agents/intraday_scanner.py` now defines `CANDLE_LOOKBACK = "2d"` and `CANDLE_INTERVAL = "5m"` at module top. 2 unit tests confirm both exist and `get_intraday_candles` no longer hides a `NameError`.

### B1. ✅ RESOLVED — `RiskManager` now receives real open positions and daily P&L
**Fixed in**: `fix/verification-findings` (HIGH-5).
**What landed**: two new helpers `get_open_position_symbols()` and `today_pnl_pct(capital)` in `agents/execution_agent.py`. `agents/master.py:run_for_stock` calls them instead of hard-coding `[]` and `0.0`. Correlation, sector-overlap, and daily-loss limits now actually fire. 6 unit tests cover the helpers and the wiring.

### B2. ✅ RESOLVED — LearningAgent applies each closed trade exactly once
**Fixed in**: `fix/verification-findings` (CRIT-2).
**What landed**: `weights_applied` flag on the `trades` table; new helpers `fetch_unweighted_closed_trades()` and `mark_trade_weights_applied()`. `main.py` learning loop only processes trades where the flag is 0 and sets it to 1 after applying.

### B3. 🟠 `signal_weights` are read but never used by the rule-based decision
**Where**: `_rule_based_decision` weighs `tech/sent/pat/winrate` with **regime-derived** weights, not the per-stock learned weights. The learned weights end up only in the LLM RAG prompt — and the LLM may or may not use them.
**Why**: the entire LearningAgent feedback loop is decorative for the deterministic path.
**Fix**: replace the regime-only weights with `regime_weight × learned_weight` element-wise; or apply learned weights as a multiplicative factor on each `*_norm` term.

### B4. ✅ RESOLVED — Slippage / brokerage centralised in `core/costs.py`
**Fixed in**: `fix/verification-findings` (MED-7).
**What landed**: `core/costs.py` exposes `SLIPPAGE_FRAC`, `BROKERAGE_FRAC`, `STT_SELL_FRAC`, `ROUND_TRIP_COST_FRAC`, `cost_per_side`, `cost_round_trip`. All callers (`execution_agent`, `backtester`, `backtest_intraday`, `backtest_gap`, `simulate_day`, `dashboard`) import from it. Lump-sum 0.06% in dashboard `pnl()` replaced with the centralised round-trip cost.
**⚠️ Side-effect**: backtest_intraday and backtest_gap previously used SLIPPAGE=0.001 (now 0.0005) — re-run those backtests; numbers will move slightly.

### B5. ✅ RESOLVED — Scheduler uses timezone-aware `datetime.now`
**Fixed in**: `fix/verification-findings` (MED-6).
**What landed**: `core/scheduler.py:job_intraday_scan` now reads `datetime.now(tz=ZoneInfo("Asia/Kolkata"))`. Gate works correctly on any machine, not just IST laptops.

### B6. 🟡 `monitor_positions()` uses a single LTP sample
**Where**: `agents/execution_agent.py:_get_ltp` calls `yf.Ticker(...).history(period="1d")`.
**Why**: during market hours, this is the live LTP — but only one sample per 5-minute monitor tick. SL/target touches that happen between ticks are missed. Outside market hours, yfinance returns the **previous close**, so SL/target evaluations are stale (mostly harmless because the market is closed, but worth noting).
**Fix**: use Groww `get_quote(symbol)` for live LTP during market hours; for SL/target, also walk yfinance 5m candles since entry to detect intraday touches between ticks.

### B7. 🟡 PatternAgent `outcome_pct` is computed off `match_end` close, not entry candle
**Where**: `agents/pattern_agent.py:_analyze`.
**Why**: outcomes are measured for "10 days after window end", not "10 days after we'd have entered". Subtle, but EV is over-estimated for windows that ended on a strong candle.
**Fix**: define an explicit entry candle (e.g. window end + 1) and shift the outcome anchor.

### B8. 🟡 NSE keyword search in BSE results false-positives
**Where**: `agents/earnings_calendar_agent.py:fetch_bse_results` uses `company.split()[0].upper() in headline.upper()`.
**Why**: "RELIANCE" matches RELIANCE INDUSTRIES, RELIANCE POWER, RELIANCE INFRA, RELIANCE COMMUNICATIONS. You will get cross-symbol filings.
**Fix**: match on the BSE scrip code (lookup table) instead, and verify with a second token.

### B9. 🟡 `EarningsCalendarAgent.score_result` only inspects the filing **subject**
**Where**: `score_result(subject, content="")` always called with `content=""`.
**Why**: NSE filings have a PDF attachment with the actual numbers. Keyword-match on "Q3 Results — Strong" vs "Q3 Results — Disappointing" is the entire signal.
**Fix**: download the attachment and run a small extraction pipeline (PyPDF2 or pdfminer + regex on revenue/profit/EPS).

### B10. ✅ RESOLVED — `ripple/config.py` portable
**Fixed in**: `fix/verification-findings` (LOW-9).
**What landed**: `OUTPUT_DIR` defaults to `Path(__file__).parent.parent / "output"`. `Config` class kept as a back-compat shim. `ripple/pipeline.py:export_to_json` reads from `ripple.config.OUTPUT_DIR` instead of a hard-coded path.

### B11. 🟡 `_fo_expiry_days` doesn't account for NSE holiday-shifted expiry
**Where**: `india_intraday_model.py:_fo_expiry_days`.
**Why**: when the last Thursday is a holiday, expiry shifts to the prior trading day. The feature is wrong on those weeks (~5–10 weeks/year). Model has been trained on noisy labels.
**Fix**: load NSE holiday calendar; subtract holidays.

### B12. ✅ RESOLVED — Agents write to `data/dynamic_watchlist.json`, not `config.yaml`
**Fixed in**: `fix/verification-findings` (LOW-10).
**What landed**: new module `core/watchlist.py` with `resolve_watchlist(config)` and `add_to_dynamic_watchlist(symbols)`. `DiscoveryAgent._add_to_watchlist` and `PreOpenMonitor._add_to_watchlist` now write to the JSON file. `core/scheduler.py:job_prune_watchlist` prunes the JSON file instead of `config.yaml`. Effective watchlist resolved at runtime as `core_watchlist + dynamic + legacy watchlist`, deduped, capped.

### B13. 🟢 `weekly_analysis` is not weekly
**Where**: `agents/learning_agent.py:weekly_analysis`.
**Why**: it queries `LIMIT 20` with no date filter. The label is misleading.
**Fix**: filter by `exit_date >= datetime('now','-7 days')` or rename.

### B14. 🟢 `master.py:scores["volume_ratio"]` defaults to `1.0` if TechnicalAgent failed
**Where**: master fan-out section.
**Why**: when TechnicalAgent errors, all defaults look "OK" → the hard filter passes silently. A failed indicator should not look the same as a healthy one.
**Fix**: set defaults to `None` and treat None as failing the filter.

### B15. (Subsumed by B0 / B0b above.)
Originally documented as "LearningAgent ignores `sector_momentum` and `regime_alignment`". The actual issue is structural: the trades table doesn't store entry-time signals at all, and even if it did, the read path crashes on `sqlite3.Row.get()`. See B0 and B0b.

---

## C. Architectural smells

### C1. 🟠 Three different backtesters
1. `core/backtester.py` (event-driven, RSI/MACD)
2. `backtest_gap.py` (gap strategy, hard-coded inside)
3. `backtest_intraday.py` (1h ML, hard-coded params at top)
4. (and the `dashboard.py` "Tab 3 — Backtest" reimplements gap logic again, as a fourth)

**Why**: forks of the same idea diverge. Slippage / cost models already differ (B4).
**Fix**: collapse all into `core/backtester.py` with strategies as classes.

### C2. 🟠 `PaperBroker` exists but is unused
**Where**: `core/broker.py:PaperBroker`. Has circuit-breaker, brokerage/STT computations, position bookkeeping — but `ExecutionAgent` writes directly to SQLite. The two abstractions overlap.
**Fix**: route execution through `Broker` so `paper`/`live` is a single switch. Today that switch is incomplete: `mode=live` raises `NotImplementedError`.

### C3. 🟠 `_load_config()` is everywhere
**Where**: `agents/risk_manager.py`, `core/scheduler.py`, several CLIs all re-open `config.yaml`. The graph report flagged this (`_load_config` has 20 edges).
**Why**: hot-reload is implicit and unintentional. Also: scheduler self-modifies `config.yaml`, and every reload picks up partial writes.
**Fix**: pass `config` through, or load once into a singleton with a file watcher if you really want hot-reload.

### C4. 🟡 Many network calls have no retry/backoff
yfinance is the worst offender; rate-limited responses look like empty data. Any agent that "no data → fall back to default" loses signal silently.
**Fix**: a small `core/http.py` with Tenacity retries (exponential, max 3) for both `requests` and `yfinance` wrappers.

### C5. 🟡 No tests
Zero `tests/` directory. `test_stock.py` is a script, not a test (it makes real DB writes).
**Fix**: at minimum, fixture-based tests for `_rule_based_decision`, RiskManager gates, PatternAgent EV math, and pre-open gap analysis. Use `pytest` + `pyfakefs` for filesystem KB.

### C6. 🟡 No structured logging or per-agent timing
Logs are formatted strings. Hard to compute "what's the median time spent in PatternAgent across 1000 runs?".
**Fix**: switch to `structlog` or use `logging.LoggerAdapter` with extras. Wrap each `Agent.run` in a timing context manager.

### C7. 🟡 Watchlist drift
`DiscoveryAgent.discover` and `PreOpenMonitor.scan` both append; `job_prune_watchlist` removes. The order operations execute matters and is not idempotent.
**Fix**: separate `core_watchlist` (immutable, user-curated) from `dynamic_watchlist` (ephemeral, regenerated daily). The system already has `core_watchlist` in config but doesn't enforce it.

### C8. 🟡 Two "intraday" notions in the same prompt
`intraday_score` is a 5m signal from `TechnicalAgent`; `intraday_ml_*` is a 1h signal from `india_intraday_model`. Both go into the LLM prompt under similar names → confused LLM.
**Fix**: rename to `tech_5m_*` and `ml_intraday_1h_*`.

### C9. 🟡 SQLite without WAL or row-level locking
`paper_trades.db` is opened/closed per call. When `main.py` runs concurrently with `monitor_positions` they race.
**Fix**: enable WAL (`PRAGMA journal_mode=WAL`); keep one long-lived connection; protect writes with a transaction.

### C10. 🟡 `ml_model.predict` re-downloads market data every call
**Where**: `predict(symbol)` calls `load_market_data(start, end)` which makes 7 yfinance requests. For a 50-symbol watchlist scan, that's 350 requests.
**Fix**: cache market context to `stocks/_market_data.parquet` keyed by date and refresh once per day.

### C11. 🟢 `requirements.txt` has `# kiteconnect==5.0.1` commented but `core/broker.py` references it
The reference is **lazy** — `from kiteconnect import KiteConnect` is inside `ZerodhaBroker.__init__` with an explicit `ImportError` handler. So the module imports cleanly without kiteconnect; the issue is just that the optional-dependency story isn't formalised.
**Fix**: extras under `pyproject.toml`: `[project.optional-dependencies] live = ["kiteconnect>=5.0.1"]`.

### C12. 🟢 No central place defines "what is a NSE symbol"
`NIFTY50_TICKERS` is duplicated in `india_intraday_model.py` and `agents/intraday_scanner.py:NIFTY50` (with different element ordering and `BAJAJ-AUTO` vs `BAJAJ_AUTO` vs `BAJAJ_AUTO`).
**Fix**: one canonical list in `core/symbols.py` with a normalisation helper.

---

## D. Performance

### D1. 🟠 Sequential sub-agent execution per stock
Each stock takes 10–25 s. For 50 stocks that's 8–20 minutes — borderline okay for the 09:00 → 09:15 gap but uncomfortable.
**Fix**: `concurrent.futures.ThreadPoolExecutor(max_workers=5)` over symbols. Network-bound so threads (not processes) are fine.

### D2. 🟡 Sector-correlation rebuild downloads 9 indices per stock per refresh
`DataAgent._compute_sector_correlation` makes ~9 yfinance calls per stock. For 50 stocks: 450 calls / `06:00` job.
**Fix**: batch-download the 9 indices once per refresh, share across all stocks.

### D3. 🟡 Pattern matching is O(N · WINDOW²) per stock
DTW over ~1000 windows per stock × ~20 stocks adds up. Fine on a laptop today; grows with `history_years`.
**Fix**: `dtaidistance` already supports a `block` parameter / NumPy fast paths; or cache pattern hashes.

---

## E. Operational / docs

### E1. 🟠 No README at repo root
A new contributor can't find the entry point.
**Fix**: write `README.md` (the new `docs/user-guide.md` covers content; surface a 30-line summary at the root).

### E2. 🟡 `pyproject.toml` is broken
`build-backend = "setuptools.backends.legacy:build"` is not a real backend. `pip install -e .` will fail.
**Fix**: `build-backend = "setuptools.build_meta"`; add `[project] dependencies = [...]` mirroring `requirements.txt`.

### E3. 🟡 `requirements.txt` pins `litellm==1.40.0` (mid-2024)
Dependencies are old. `transformers==4.40.0` is also pinned and below FinBERT's requirements for some configurations.
**Fix**: bump and test, or move to `pip-compile` / `uv lock`.

### E4. 🟢 `paper_trades.db` is committed to git history
The file is in `.gitignore` but the repo state shows an older copy was pushed previously (it currently exists in working dir but git status would tell). If any commit included it, that data is in git history.
**Fix**: confirm via `git log --oneline -- paper_trades.db`; if present, rewrite with `git filter-repo` or `bfg` and force-push. The DB has no PII but exposes your trading style.

### E5. 🟢 No `pyproject.toml` linting/format config
No ruff/black/isort. Style varies across files.
**Fix**: add `ruff` with a simple config + a pre-commit hook.

---

## F. Modelling / strategy concerns (separate from bugs)

These are not "bugs" but are worth examining:

- **Survivorship bias.** ML training uses today's NIFTY 50 over 3 years of intraday history. Stocks dropped from the index are gone from `models/stocks_1h/`. Backtest win-rates are biased upward.
- **Label leakage.** Both ML models use `future_return > threshold`. Features are computed at time `t`; labels at `t + horizon`. As long as `dropna().iloc[:-FORWARD_DAYS]` is honoured (it is), this is okay — but verify on every data shape change.
- **Regime classifier uses NIFTY only.** A stock-specific regime would be more useful for bear/range stocks within a bull market. (See `06-improvements.md`.)
- **No portfolio P&L attribution.** P&L is per trade; you can't tell which signals (technical, ML, gap, pattern) contributed.
- **No paper-vs-live calibration.** The system targets `mode=paper` and there is no parity check between paper fills and the (hypothetical) live broker.
- **Capital is ₹10,000.** With min-1-share quantity and Indian large-caps trading at ₹1k–4k, position sizing often results in 1–3 shares — which doesn't match the EV math of the patterns/ML (which assume continuous sizing).
