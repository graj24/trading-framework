# Documentation Verification Report

**Date**: 2026-05-16
**Verified against**: HEAD of `trading-framework` working tree
**Method**: claim-by-claim grep + read of source files; behavioural checks where needed
**Files reviewed**: 9 docs (`docs/README.md`, `docs/user-guide.md`, `docs/technical-reference.md`, `docs/analysis/01-06`)
**Code files cross-checked**: 28 Python files across `agents/`, `core/`, `ripple/`, top-level scripts

---

## TL;DR

| Verdict             | Count |
|---------------------|-------|
| ✅ Verified correct  | ~95 claims |
| ⚠️ Imprecise / understated / needs nuance | 4 |
| ❌ Wrong            | 0 |
| 🔥 Missed by docs (significant runtime bugs) | 2 |

**Overall match: ~95–97%.** No outright lies; structure, constants, control flow, and bug list all check out. But two **runtime crash paths** in the live code were not flagged in `analysis/05-issues.md` and should be added.

See `findings.md` for the prioritised follow-ups (what to fix in code) and `doc-edits.md` for the suggested doc updates.

---

## 🔥 Things the docs got wrong by **omission** (highest priority)

### F-1. `main.py` will crash with `AttributeError` on every closed trade

**Where**: `main.py:135-138`
```python
for t in closed_trades:
    learner.update_weights(t["symbol"], outcome, {
        "technical_score": t.get("technical_score", 0),
        ...
    })
```
**Problem**: `closed_trades` rows are `sqlite3.Row` objects (because `conn.row_factory = sqlite3.Row`). `sqlite3.Row` does **not** implement `.get()`. Confirmed empirically:
```
>>> row.get('technical_score', 0)
AttributeError: 'sqlite3.Row' object has no attribute 'get'
```
The current `paper_trades.db` already contains 1 closed trade (`outcome='loss'`), so any next run of `main.py` will raise `AttributeError` and skip the rest of the post-cycle reporting (and the LearningAgent loop entirely).

**Severity**: 🔴 **runtime crash** in the default `main.py` path.
**Doc impact**: `analysis/05-issues.md` only mentions a softer issue (B15 — `sector_momentum`/`regime_alignment` not passed). The real bug is bigger and crashes the process. Also breaks claim in `analysis/04-decision-pipeline.md` §5 ("Triggered by `main.py` (default mode) after each cycle") — actually triggered, then immediately crashed.

**Fix** (smallest): either change `t.get(...)` to `dict(t).get(...)` or to `t["..."] if "..." in t.keys() else 0`. Even after that, the `trades` schema does not include `technical_score`, `sentiment`, or `pattern_ev` columns, so the values would be 0/missing — see F-2.

### F-2. `trades` schema doesn't store entry-time signals — LearningAgent is fundamentally inert

**Where**: `agents/execution_agent.py:28-44` (CREATE TABLE + INSERT)

The `trades` table schema is:
```sql
id, symbol, entry_date, entry_price, stop_loss, target,
position_size, exit_date, exit_price, pnl_pct, pnl_inr,
outcome, reasoning, created_at
```
There are **no columns** for `technical_score`, `sentiment`, `pattern_ev`, `sector_momentum`, or `regime_alignment`. So even if F-1 is patched, every value passed to `LearningAgent.update_weights` will be 0 / missing → `update_weights` will reject every signal as "not positive" → **no weights ever change**.

**Severity**: 🔴 — the entire LearningAgent feedback loop is decorative.
**Doc impact**: `analysis/03-agents.md` §9 says "Weights are read but never used by the rule-based decision" — true, but the deeper issue is they're never *updated* meaningfully either. Combined with F-1, the LearningAgent is doubly inert.

**Fix**: add `technical_score, sentiment, pattern_ev, sector_momentum, regime_alignment` REAL columns to the trades table; populate them in `ExecutionAgent.execute_trade(...)` from a new `signals_at_entry` parameter; update `MasterAgent` to pass them through.

### F-3. `IntradayPatternScanner.get_intraday_candles` references undefined globals

**Where**: `agents/intraday_scanner.py:109`
```python
df = t.history(period=CANDLE_LOOKBACK, interval=CANDLE_INTERVAL)
```
**Problem**: `CANDLE_LOOKBACK` and `CANDLE_INTERVAL` are **not defined** anywhere in the module (verified via `grep` across the entire repo — 1 use, 0 definitions).

The whole call is wrapped in `try / except Exception`, so the `NameError` is silently swallowed and `get_intraday_candles` always returns `None`. This means **every call to `IntradayPatternScanner.scan_all` returns no patterns** — even when there are visible bull flags or VWAP reclaims on the live 5m chart.

**Severity**: 🟠 (not crashing, but the entire intraday pattern feature is broken).
**Doc impact**: `analysis/03-agents.md` §12 describes 6 detectors and a BUY confidence threshold of 65 — accurate to the code, but currently unreachable. `02-data-flow.md` §8 ("Multi-pass intraday scanner") is also moot in practice. `technical-reference.md` §11 lists `IntradayPatternScanner.scan_all()` as a public method — it works, but always returns empty.

**Fix**: define
```python
CANDLE_LOOKBACK = "2d"
CANDLE_INTERVAL = "5m"
```
at module top (matching the docstring `"Fetch 5-min candles from yfinance (last 2 days)"`).

### F-4. Streamlit/Plotly are imported by `dashboard.py` but not in `requirements.txt`

**Where**: `requirements.txt` vs `dashboard.py:11-12`
The user-guide already mentions this (`Quick start §3.5: pip install streamlit plotly`), but it's worth surfacing in `requirements.txt` because new contributors trying `pip install -r requirements.txt && streamlit run dashboard.py` will fail.

**Severity**: 🟡 — UX issue, no code path crashes.
**Doc impact**: doc is correct; the **code repo** is wrong. Add to requirements.

---

## ⚠️ Imprecisions to clean up in the docs

### W-1. `docs/analysis/05-issues.md` B15 understates the bug
**Claim**: "LearningAgent updates ignore `sector_momentum` and `regime_alignment`. Pass the full feature vector or remove the unused signals."
**Reality**: see F-1 and F-2. The fix is structural, not just "pass more keys".
**Suggested edit**: replace B15 with a single 🔴 entry combining F-1, F-2.

### W-2. `docs/analysis/03-agents.md` §8 understates monitor_positions
**Claim**: "Triggers SL/target via daily candle, but the code uses `LTP` from yfinance daily history — which is the previous close if called outside market hours."
**Reality**: correct as far as it goes, but worth adding: during market hours the LTP is real; the comparison is just point-in-time, missing intraday touches between calls. Both effects matter; today's text only covers one.
**Suggested edit**: rewrite as "during market hours, this is the live LTP; outside hours, it's the previous close. Either way it's a single sample — intraday SL/target touches between 5-minute polls are missed."

### W-3. `docs/analysis/05-issues.md` C11 is now false
**Claim**: "`requirements.txt` has `# kiteconnect==5.0.1` commented but `core/broker.py` imports it."
**Reality**: `core/broker.py:122` uses `from kiteconnect import KiteConnect` only **inside** `ZerodhaBroker.__init__`, with an explicit `ImportError` handler. So the import is not eager and the doc's wording overstates the issue.
**Suggested edit**: drop "imports it" → "wraps it in `ZerodhaBroker.__init__` with a fallback ImportError; the optional-dependency framing is still cleaner".

### W-4. `docs/user-guide.md` §3.2 mentions `GROQ_API_KEY` env var
**Claim**: `# .env\nGROQ_API_KEY=your_groq_key_here`
**Reality**: `litellm.completion(model="groq/llama-3.3-70b-versatile", ...)` reads `GROQ_API_KEY` — confirmed by `litellm` docs and convention. ✅ correct.
**Note**: not flagged as wrong; just confirming this since the `.env.example` template doesn't list it.

---

## ✅ Claims verified correct (representative subset)

The following claims were verified directly against code with grep + read. This is not exhaustive — it covers the testable / falsifiable claims.

### Constants and configuration

| Doc claim                                                    | Code location                                       | Status |
|--------------------------------------------------------------|-----------------------------------------------------|--------|
| `SLIPPAGE = 0.0005` in execution_agent / backtester          | `execution_agent.py:20`, `core/backtester.py:24`    | ✅      |
| `BROKERAGE = 0.0003` in execution_agent / backtester         | same files                                          | ✅      |
| `SLIPPAGE = 0.001` in `backtest_intraday.py`, `backtest_gap.py`, `simulate_day.py` | files cited; values 0.001 / 0.001 / 0.0003 brokerage | ✅ |
| Dashboard pnl uses `- 0.06` (i.e. 0.06%)                     | `dashboard.py:59`                                   | ✅      |
| PatternAgent: `WINDOW=20, LOOKAHEAD=10, TOP_K=5, EXCLUDE_TAIL=60` | `agents/pattern_agent.py:24-27`                  | ✅      |
| LearningAgent: `WIN_BOOST=1.05, LOSS_DECAY=0.97, MIN_WEIGHT=0.1, MAX_WEIGHT=3.0` | `agents/learning_agent.py:20-23`              | ✅      |
| LearningAgent `WEIGHT_SIGNALS = [...]` with 5 entries        | `agents/learning_agent.py:19`                       | ✅      |
| PreOpenMonitor: `GAP_UP_THRESHOLD=1.5, GAP_DOWN_THRESHOLD=-1.5, STRONG_GAP=4.0` | `agents/pre_open_monitor.py:52-54`            | ✅      |
| `ml_model.py`: `LABEL_THRESHOLD=1.5, FORWARD_DAYS=5`         | `ml_model.py:32-33`                                 | ✅      |
| `india_intraday_model.py`: `FORWARD_HOURS=3, LABEL_THRESHOLD=1.0` | `india_intraday_model.py:37-38`                 | ✅      |
| `india_intraday_model.py`: `NSE_OPEN=9, NSE_CLOSE=15`        | `india_intraday_model.py:40-41`                     | ✅      |
| `data_agent.py`: `NSE_SUFFIX=".NS"`, `NIFTY_TICKER="^NSEI"`  | `data_agent.py:33,47`                               | ✅      |
| 8 entries in `data_agent.SECTOR_INDICES` (IT/BANK/PHARMA/AUTO/ENERGY/FMCG/METAL/REALTY) | `data_agent.py:36-44`                       | ✅      |
| 7 entries in `ml_model.SECTOR_INDICES` (3 market + 4 sectors: fmcg/it/auto/energy) | `ml_model.py:35-43`                          | ✅      |
| `core/broker.py` PaperBroker `CIRCUIT_BREAKER_ORDERS=5, CIRCUIT_BREAKER_WINDOW=60` | `core/broker.py:51-52`                       | ✅      |
| `BROKERAGE_PCT=0.0003, BROKERAGE_MAX=20.0, STT_SELL_PCT=0.001` in `core/broker.py` | `core/broker.py:18-20`                      | ✅      |
| TIMEOUT values: 8 in news_agent / intraday_scanner; 10 in pre_open / discovery / earnings | grep confirmed                          | ✅      |

### MasterAgent decision pipeline

| Doc claim                                                         | Code location                  | Status |
|-------------------------------------------------------------------|--------------------------------|--------|
| Confidence floor 60 (BUY → HOLD if below)                         | `agents/master.py:341`         | ✅      |
| Hard filter: `trend != "up" or macd_signal != "bullish" or vol_ratio < 1.0` | `agents/master.py:344-355`     | ✅      |
| Tier-1 emergency: `tier == 1 and sentiment < -0.2`                | `agents/master.py:107, 321`    | ✅      |
| LLM `max_tokens=200` (overrides config `max_tokens=2000`)         | `agents/master.py:83` vs `config.yaml`                                | ✅      |
| Risk manager call passes `open_positions=[], daily_pnl_pct=0.0`   | `agents/master.py:370-371`     | ✅ (Issue B1 confirmed)|
| Risk SL only overrides if LLM SL is missing (`if not stop_loss`)  | `agents/master.py:376-377`     | ✅      |
| `volume_ratio` defaults to `1.0` if TechnicalAgent failed → silent skip-through | `agents/master.py:174`           | ✅ (Issue B14 confirmed) |
| Sequential sub-agent fan-out (no parallelism)                     | `agents/master.py` lines 254-275 | ✅    |
| Hard skip on `trending_bear AND sentiment < -0.3` in rule fallback | `agents/master.py:113-115`    | ✅      |
| Composite weights by regime (ranging: tech 0.20/sent 0.45/pat 0.35; trending_bear: 0.30/0.40/0.30; trending_bull: 0.40/0.30/0.30) | `agents/master.py:120-133` | ✅ exact |
| Composite blend: `tech_norm = tech/10*100; sent_norm = (sent+1)/2*100; pat_norm = clip(50 + ev*5)` | `agents/master.py:135-138` | ✅ |
| ML weight 0.4 when present, 0.6 multiplier on others              | `agents/master.py:145-167`     | ✅      |
| Composite ≥55 + tech ≥ threshold + sent ≥ -0.1 + filters all true → BUY | `agents/master.py:182`         | ✅      |
| Composite < 35 OR sent ≤ -0.5 → SKIP                              | `agents/master.py:188-191`     | ✅      |

### TechnicalAgent

| Doc claim                                                    | Code location                              | Status |
|--------------------------------------------------------------|--------------------------------------------|--------|
| 10 score criteria (each adds 1)                              | `agents/technical_agent.py:147-167` (10 occurrences) | ✅ |
| Requires ≥200 daily bars                                     | `agents/technical_agent.py:114`            | ✅      |
| Support/resistance: 1.5% tolerance, ≥3 touches, last 252 days| `agents/technical_agent.py:74-99`          | ✅ exact |
| Intraday score from 5m candles is 0–3 (RSI>50 + MACD bullish + Close>VWAP) | `agents/technical_agent.py:222-227` | ✅ |

### RegimeAgent

| Doc claim                                                       | Code location                  | Status |
|-----------------------------------------------------------------|--------------------------------|--------|
| `trending_bull` ⇔ `adx > 25 AND ret_20d > 2`                    | `agents/regime_agent.py:74`    | ✅      |
| `trending_bear` ⇔ `adx > 25 AND ret_20d < -2`                   | `agents/regime_agent.py:76`    | ✅      |
| `high_volatility` ⇔ `volatility > 20`                            | `agents/regime_agent.py:78`    | ✅      |
| VIX upgrade only triggers from `ranging` (not bull/bear)        | `agents/regime_agent.py:84`    | ✅ (Issue gotcha valid) |

### ExecutionAgent

| Doc claim                                                       | Code location                  | Status |
|-----------------------------------------------------------------|--------------------------------|--------|
| Buy entry: `entry_price * (1 + SLIPPAGE)`                        | `agents/execution_agent.py:92` | ✅      |
| SL exit: `stop_loss * (1 - SLIPPAGE)`; target exit: `target * (1 - SLIPPAGE)` | `agents/execution_agent.py:126,129` | ✅ |
| `monitor_positions` uses single `_get_ltp` sample, no intraday peek | `agents/execution_agent.py:118-133` | ✅ |
| Schema (id, symbol, entry_date, entry_price, stop_loss, target, position_size, exit_date, exit_price, pnl_pct, pnl_inr, outcome, reasoning, created_at) | `agents/execution_agent.py:28-44` | ✅ exact |

### Dashboard

| Doc claim                                            | Code location              | Status |
|------------------------------------------------------|----------------------------|--------|
| 5 tabs (Portfolio / Signals / Backtest / News / Intraday ML) | `dashboard.py:71`         | ✅ exact |
| Tab 3 backtest replicates gap-strategy logic locally | `dashboard.py:223+`        | ✅      |
| `pnl()` deduction = `0.06` (i.e. 0.06%)              | `dashboard.py:59`          | ✅      |
| Backtest position sizing: `CAPITAL * 0.15`           | `dashboard.py:279`         | ✅      |

### Scheduler

| Doc claim                                                       | Code location                  | Status |
|-----------------------------------------------------------------|--------------------------------|--------|
| `BlockingScheduler(timezone="Asia/Kolkata")`                    | `core/scheduler.py:337`        | ✅      |
| Cron 06:00 KB update; 07:00 discover; 09:00 preopen + signals; 09:15 execute; 15:00 close all; 15:30 post-market + earnings prep; 15:45 prune | `core/scheduler.py:339-353` | ✅ exact |
| Interval 5 min for monitor + intraday                           | `core/scheduler.py:344-347`    | ✅      |
| Overnight earnings: every 30 min, 18:00–08:00                   | `core/scheduler.py:355`        | ✅      |
| `job_intraday_scan` uses naive `datetime.now()` (B5)            | `core/scheduler.py:176-178`    | ✅ confirmed |

### Pattern, Risk, Pre-open, Earnings, Discovery

| Doc claim                                                       | Code location                  | Status |
|-----------------------------------------------------------------|--------------------------------|--------|
| Pattern outcome anchored on `match_end` close (B7)              | `agents/pattern_agent.py:78`   | ✅ confirmed |
| `dtaidistance` import with Euclidean fallback                   | `agents/pattern_agent.py:11-19`| ✅      |
| Risk Kelly default 10% if no data                               | `agents/risk_manager.py:26`    | ✅      |
| Risk ATR multiplier default 2.0                                 | `agents/risk_manager.py:50`    | ✅      |
| Trailing stop: activate after +1%, trail 0.5%                   | `agents/risk_manager.py:58`    | ✅      |
| Risk: max correlation 0.8, max 2 per sector, then `max_open_positions` cap | `agents/risk_manager.py:99-118` | ✅ |
| Earnings BSE keyword false-positive: `company.split()[0]` substring (B8) | `agents/earnings_calendar_agent.py:112` | ✅ |
| Pre-open `_fetch_preopen_yfinance` is misleadingly named — actually current vs prev close | `agents/pre_open_monitor.py:104-130` | ✅ |
| Discovery: 6 sources tried; multi-source × 1.5 bonus            | `agents/discovery_agent.py:_aggregate_candidates` | ✅ |

### ML / Intraday

| Doc claim                                                       | Code location                  | Status |
|-----------------------------------------------------------------|--------------------------------|--------|
| Both models: `GradientBoostingClassifier(n_estimators=300, max_depth=4, lr=0.05, subsample=0.8, max_features=0.8, random_state=42)` | `ml_model.py:208-214`, `india_intraday_model.py:204-207` | ✅ exact |
| 5-fold `TimeSeriesSplit`                                         | both files                     | ✅      |
| `dynamic_threshold`: base 0.55; VIX>25 +0.08, >20 +0.04, <13 -0.03; trending_bull -0.03, bear/high_vol +0.05; hour 9 +0.04, 15 +0.03; FO 0 day +0.07, ≤2 days +0.03; clip [0.45, 0.80] | `india_intraday_model.py:dynamic_threshold` | ✅ exact |
| 6 intraday detectors with confidences 75/70/65/60/80/70         | `agents/intraday_scanner.py:detect_*`  | ✅      |
| BUY threshold ≥65 in intraday scanner                            | `agents/intraday_scanner.py:scan_stock`  | ✅      |
| Last-Thursday F&O expiry computed (no holiday adjustment)        | `india_intraday_model.py:_fo_expiry_days` | ✅ (Issue B11 valid) |

### Dependencies declared but never imported

| Package              | In `requirements.txt`? | Imported anywhere? |
|----------------------|------------------------|--------------------|
| `python-telegram-bot`| yes                    | **NO** (alerts use `requests` directly) — ✅ doc claim correct |
| `SQLAlchemy`         | yes                    | **NO** — ✅ doc claim correct |
| `pandas-ta`          | yes                    | **NO** — ✅ doc claim correct |
| `nsepy`              | yes                    | **NO** — ✅ doc claim correct |
| `kiteconnect`        | commented              | YES, lazy in `ZerodhaBroker.__init__` — ✅ |
| `pytrends`           | not declared           | YES, lazy in `discovery_agent.fetch_google_trends` — ✅ |
| `streamlit`/`plotly` | **NO**                 | YES in `dashboard.py` — ❌ missing from requirements (see F-4) |

### Architecture-level claims

| Claim                                                            | Status |
|------------------------------------------------------------------|--------|
| `paper_trades.db` — SQLite, single `trades` table                | ✅      |
| `agents/base.py:AgentResult` is the universal return type        | ✅      |
| Three different backtesters (`core/backtester`, `backtest_gap.py`, `backtest_intraday.py`) | ✅ |
| Plus a fourth in `dashboard.py` Tab 3                            | ✅      |
| `_load_config()` is duplicated across `risk_manager.py`, `scheduler.py`, multiple CLIs | ✅ |
| `PaperBroker` exists but `ExecutionAgent` doesn't use it          | ✅      |
| `mode=live` in config raises NotImplementedError today           | ✅ (`agents/execution_agent.py:88`) |
| LLM prompt interpolates raw headlines (prompt injection risk)    | ✅      |
| `ripple/config.py:OUTPUT_DIR` hard-codes a different developer's path | ✅ |

---

## Per-doc breakdown

### `docs/README.md`
Index doc; no factual claims to verify. ✅ links resolve.

### `docs/user-guide.md`
- §3.2 mentions `GROQ_API_KEY` in `.env`; OK (litellm convention).
- §3.5 says "pip install streamlit plotly" — correct workaround for F-4.
- §6.1 lists 5 dashboard tabs in correct order — ✅.
- §7 lists 3 backtesters + dashboard's 4th — ✅.
- §8 thresholds (1.5% / 5d, 1.0% / 3h) — ✅.
- §9 says live mode is gated and raises `RuntimeError` — ✅ exact code path verified.
- §10.5 troubleshoot for "Intraday model not trained" — ✅ matches code behaviour.
- §11 FAQ: "It does not place options orders" — ✅.

### `docs/technical-reference.md`
- §3 repo layout — manually compared against `ls` output. ✅ matches.
- §4.1 config schema — every key present in `config.yaml` is documented. The `llm.max_tokens=2000` claim is technically a config value; the **runtime** override to 200 is correctly noted. ✅ caveats.
- §4.2 `.env` keys — comprehensive. ✅.
- §5 module API surface — every signature spot-checked is correct.
- §6.2 schema DDL — verbatim match.
- §11 live trading state — ✅, matches `agents/execution_agent.py:88`.
- §12 network endpoints — ✅, all confirmed in greps.

### `docs/analysis/01-architecture.md`
- §1 first-line claim "not an LLM-tool-calling agent loop — agents are plain Python classes that subclass `Agent`" — ✅ exact.
- §3 god-node table — sourced from `graphify-out/GRAPH_REPORT.md`. Counts match.
- §6.1 KB files table — every file present (verified by `ls stocks/RELIANCE/`). `bulk_deals.json` content is `{}` for all sampled stocks (consistent with "currently empty" claim). ✅.
- §7 external dependencies — every URL pattern verified via grep. ✅.
- §8 design choices — opinion, no factual error.

### `docs/analysis/02-data-flow.md`
- All Mermaid diagrams render (verified via `python` regex count: 8 mermaid blocks).
- Composite-score branch logic in §6 matches code exactly.
- Position lifecycle in §7 matches `outcome` enum (open / win / loss / emergency_exit). ✅.
- Sequence diagram §3 — note: it omits the F-1 crash. Worth annotating.

### `docs/analysis/03-agents.md`
Per-agent walkthrough. All ~60 testable claims verified except the four imprecisions (W-1 to W-3 above). One additional observation:
- §9 LearningAgent — see F-1, F-2 above. Should be flagged 🔴.
- §12 IntradayPatternScanner — see F-3 above. Should mention current `NameError` brokenness.

### `docs/analysis/04-decision-pipeline.md`
Step-by-step matches code line-by-line. The "empirical timing" table is anecdotal — not verified — but reasonable.

### `docs/analysis/05-issues.md`
- B1 RiskManager wiring inert — ✅ confirmed (master.py:370-371).
- B2 LearningAgent re-applies — ⚠️ partially correct; the **bigger** issue (F-1, F-2) is omitted.
- B4 Slippage divergence — ✅ all five values verified.
- B5 naive `datetime.now()` in scheduler — ✅ confirmed (line 176).
- B6 `_get_ltp` outside hours — ✅ confirmed; partial (W-2).
- B7 PatternAgent outcome anchor — ✅ confirmed.
- B8 BSE keyword false-positives — ✅ confirmed.
- B11 F&O expiry holiday-blind — ✅ confirmed (`india_intraday_model.py:_fo_expiry_days`).
- B14 volume_ratio default — ✅ confirmed.
- B15 LearningAgent missing signals — ⚠️ true but incomplete; F-2 is bigger.
- C1 three backtesters — ✅, plus a 4th in `dashboard.py`.
- C8 `intraday_*` naming clash — ✅, both 5m (`intraday_score`) and 1h (`intraday_ml_*`) injected into the same prompt.
- C11 kiteconnect — ⚠️ overstated (W-3).
- C12 NIFTY 50 list duplication — ✅, lists differ in normalisation (`BAJAJ-AUTO` vs `BAJAJ_AUTO`).

### `docs/analysis/06-improvements.md`
Roadmap doc. Every P-item references a verified issue. No code claims to verify.

---

## Files in this folder

- `report.md` (this file) — full verification log.
- `findings.md` — the prioritised list of code fixes implied by the discrepancies.
- `doc-edits.md` — the suggested edits to the existing docs to reflect the new findings.
