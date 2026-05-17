# 03 — Agent Deep-dive (Internal Analysis)

> Structured per-agent reference. Each section: what it does, exact inputs/outputs, important constants, gotchas.

---

## 0. The base contract

```python
# agents/base.py
class AgentResult:
    agent_name: str
    status: AgentStatus            # IDLE | RUNNING | DONE | ERROR
    data: dict[str, Any]
    error: Optional[str]
    timestamp: datetime
    def ok(self) -> bool: return status == DONE

class Agent(ABC):
    name, config, _status
    @abstractmethod
    def run(self, context: dict | None = None) -> AgentResult
    def status() -> AgentStatus
    def report() -> dict
    def _result(data: dict) -> AgentResult   # helper
    def _error(msg: str) -> AgentResult      # helper
```

Every agent is constructed with `config` (a dict — usually the parsed `config.yaml`) and called via `run({...})`. Return values are inspected via `.ok()` and `.data`.

---

## 1. `MasterAgent` — `agents/master.py`

**Purpose**: orchestrate all sub-agents, build a feature dict, run RAG context retrieval, call the LLM (with rule fallback), enforce hard filters, and run the risk manager.

**Public methods**:
- `run(context)` — proxies to `run_for_stock(symbol)` if `symbol` in context
- `run_for_stock(symbol)` — full pipeline for one stock

**Inputs**: `symbol` (NSE root, e.g. `RELIANCE`).
**Output `data`**: `{symbol, decision (BUY|HOLD|SKIP), confidence (0-100), entry_price, stop_loss, target, position_size, reasoning, agent_scores}`.

**Hard-coded behaviour**:
- LLM model: `groq/llama-3.3-70b-versatile` (config-overridable).
- Confidence floor for BUY: **60**.
- Hard filters on BUY: `trend == "up"`, `macd_signal == "bullish"`, `volume_ratio >= 1.0`.
- Tier-1 emergency: skip if `tier == 1 AND sentiment < -0.2`.
- ML prediction soft-fails (try/except, sets `None`).

**Important details**:
- `scores["volume_ratio"]` defaults to `1.0` if `tech_result` failed — meaning the volume filter passes by default if the technical agent broke. **This is a silent skip-through risk.**
- Risk manager only runs on BUY. The system has **no SELL** logic — it never opens shorts.
- LLM prompt is built fresh each call. There's no caching.

---

## 2. `DataAgent` — `agents/data_agent.py`

**Purpose**: build / refresh the per-stock knowledge base from Yahoo Finance.

**Public methods**:
- `build_kb(symbol)` — full KB refresh
- `load_price_history(symbol)` — convenience read of parquet

**Files written**: `price_history.parquet`, `fundamentals.json`, `earnings_history.json`, `corporate_actions.json`, `sector_correlation.json`, `event_reactions.json`, `signal_weights.json` (only if not present).

**Constants**:
- `NSE_SUFFIX = ".NS"` — yfinance ticker suffix for NSE.
- `SECTOR_INDICES` map (8 sector indices like `^CNXIT`, `^NSEBANK`).
- `history_years` from config (default 5).

**Behaviour notes**:
- **Incremental** for price history (only fetches from `last_date + 1`).
- **Non-incremental** for everything else — fundamentals, earnings, splits, sector correlations are fully refetched.
- Sector correlation fetches the **full history** of every sector index for every stock — 9 yfinance calls per stock per refresh.
- Price reaction is computed using nearest-trading-day search; if a quarterly date falls on a non-trading day, it uses the next available close vs the prior close.

---

## 3. `NewsAgent` — `agents/news_agent.py`

**Purpose**: fetch news, score sentiment, classify into TIER 1/2/3, persist headlines.

**Sources** (current state):
- `_fetch_yahoo_news(symbol)` — works.
- `_scrape_moneycontrol(symbol)` — **stub: returns `[]`**.
- `_scrape_economic_times(symbol)` — **stub: returns `[]`**.
- `_scrape_nse_announcements(symbol)` — **stub: returns `[]`**.

**Scoring**:
- Sentiment: FinBERT (`ProsusAI/finbert` via `ripple/sentiment_analyzer.py`). Returns `(Positive% - Negative%) / 100` → `[-1, 1]`. Falls back to keyword-based scoring if FinBERT errors.
- Tier (keyword-based): TIER 1 keywords include `fraud, resign, accident, bankrupt, regulatory, scam, sebi action`. TIER 2: `miss, downgrade, guidance cut, …`.

**Storage**: appends to `news_history.json`, deduplicating by headline, **truncates to last 500**.

**Public extras**: `monitor_open_positions(symbols)` returns `{symbol: tier}` for any TIER ≤ 2 events — used by the position monitor.

**Gotchas**:
- Sentiment is averaged across **all** fetched headlines, not weighted by recency or source. If Yahoo returns 15 stale results plus 1 fresh negative one, the negative gets diluted.
- Tier classification only catches keywords — doesn't understand context (e.g. *"resigns to a new role at Apple"* → TIER 1).

---

## 4. `TechnicalAgent` — `agents/technical_agent.py`

**Purpose**: compute daily indicators + intraday 5m supplement; produce a 0–10 composite score.

**Inputs**: `stocks/<SYM>/price_history.parquet` (must have ≥200 rows).
**Output `data`** (the keys MasterAgent reads):
- `technical_score` (0–10), `rsi`, `macd_signal` (bullish/bearish/neutral), `trend` (up/down/sideways), `volume_ratio`, `current_price`
- Optional: `intraday_rsi5`, `intraday_macd`, `intraday_score` (0–3), `intraday_vs_vwap`
- Plus: `support_levels`, `resistance_levels`, `bos_detected`, `ema20/50/200`, `adx`, `atr`

**Composite scoring** (each criterion = 1 point):
1. price > EMA20
2. price > EMA50
3. price > EMA200
4. RSI in [40, 60]
5. MACD line > signal line
6. price > VWAP (last 20 bars)
7. OBV rising over last 5 days
8. ADX > 25
9. price < BB upper × 0.98
10. ATR/price < 0.02

**Intraday confirmation**: if 5m intraday score ≥ 2 AND daily MACD bullish, technical_score is bumped up by 1 (capped at 10).

**Gotchas**:
- All indicators use `.ewm` Wilder-style — fine.
- `_find_support_resistance` uses 1.5% tolerance with min 3 touches over the last 252 days. Works but ignores volume at the level.
- Intraday section silently swallows exceptions (`try/except: pass`).

---

## 5. `PatternAgent` — `agents/pattern_agent.py`

**Purpose**: find historical 20-day patterns most similar to the current 20-day window using DTW (Dynamic Time Warping); compute Expected Value (EV) of trading on this pattern.

**Constants**:
- `WINDOW = 20` days
- `LOOKAHEAD = 10` days (outcome horizon)
- `TOP_K = 5` matches
- `EXCLUDE_TAIL = 60` days (don't match against the recent past)

**Algorithm**:
1. Normalise current 20d closes to (mean=0, std=1).
2. For each window of 20 closes in `[0, len-EXCLUDE_TAIL-WINDOW-LOOKAHEAD]`, compute DTW distance to current window.
3. Take top 5 nearest.
4. For each, compute `outcome_pct = (price[end+10] - price[end]) / price[end] × 100`.
5. EV = `(P(win) × avg_win) + (P(loss) × avg_loss)`.

**Output `data`**: `{symbol, pattern_match, expected_value, win_rate, similar_count}`.

**Side-effect**: writes `stocks/<SYM>/patterns.json`.

**Gotchas**:
- Uses `dtaidistance.dtw.distance` if installed, else Euclidean fallback (a regression in pattern quality).
- 5 matches is a small sample → high variance. EV ranges from −5% to +5% can flip easily across days.
- No statistical significance test on EV.

---

## 6. `RegimeAgent` — `agents/regime_agent.py`

**Purpose**: classify the broad NIFTY market regime.

**Regimes**:
- `trending_bull`: ADX > 25 AND 20d return > +2%
- `trending_bear`: ADX > 25 AND 20d return < −2%
- `high_volatility`: 20d annualised vol > 20% (or India VIX > 20 overrides ranging)
- `ranging`: anything else

**Strategy adjustments** (used as guidance, not enforced):
| Regime           | size_mult | prefer            | avoid              |
|------------------|-----------|-------------------|--------------------|
| trending_bull    | 1.2       | breakouts         | mean_reversion     |
| trending_bear    | 0.5       | short_or_cash     | longs              |
| high_volatility  | 0.5       | tight_stops       | overnight          |
| ranging          | 0.8       | mean_reversion    | breakouts          |

**Gotchas**:
- Network call to download Nifty + VIX every run. No caching.
- VIX confirmation for high-vol upgrade only triggers when current regime is `ranging` (so a `trending_bull` with VIX 22 is not re-classified).

---

## 7. `RiskManager` — `agents/risk_manager.py`

**Purpose**: position sizing + portfolio-level gating.

**Components**:
- **Half-Kelly sizing**: `b = avg_win/avg_loss; kelly = (b·p − q)/b; size = capital × kelly × kelly_fraction`. Clipped at 0 below.
  - Defaults if data missing: 10% of capital.
- **ATR-based SL**: `entry − ATR × 2.0` (computed from last 14 daily bars).
- **Trailing stop**: activate after +1% profit, trail 0.5% below current.
- **Loss limits** (from `config.yaml:risk`):
  - daily ≥ 3% → hard stop trading
  - weekly ≥ 7% → halve sizes (warning, not stop)
  - monthly ≥ 15% → flag (no automatic action besides log)
- **Correlation check**: pairwise dot product of correlation vectors with each open position. If average correlation > 0.8 → block.
- **Sector overlap**: maximum 2 positions per sector. Plus `max_open_positions` (default 3).

**Output**: `{allowed: bool, position_size, stop_loss, reason}`.

**Gotchas**:
- The "weekly loss → halve sizes" path returns `True` (allowed) but the `size_multiplier` halving happens via a string-match on `reason`: `if "reducing" in reason.lower()`. Brittle.
- `_load_config()` reloads `config.yaml` on every gate check — see issues.

---

## 8. `ExecutionAgent` — `agents/execution_agent.py`

**Purpose**: paper-trade execution, position monitoring, daily reports. Backed by SQLite (`paper_trades.db`).

**Constants**:
- `SLIPPAGE = 0.0005` (5bps each side).
- `BROKERAGE = 0.0003` (3bps each side, used in `_pnl`).

**Public methods**:
- `execute_trade(symbol, entry, sl, target, size, reasoning)` — INSERT (`outcome='open'`).
- `monitor_positions()` — loops opens, closes if `LTP <= sl` or `LTP >= target`.
- `emergency_exit(symbol, reason)` — closes immediately at LTP.
- `daily_report()` — today's stats.

**Schema** (one table `trades`):
```
id TEXT PRIMARY KEY, symbol, entry_date, entry_price, stop_loss, target,
position_size, exit_date, exit_price, pnl_pct, pnl_inr,
outcome ('open'|'win'|'loss'|'emergency_exit'), reasoning, created_at
```

**Gotchas**:
- `_get_ltp` calls yfinance — slow, rate-limited. Should use Groww when available.
- Triggers SL/target via daily candle, but the code uses `LTP` from yfinance daily history — which is the **previous close** if called outside market hours. So if you run `monitor_positions()` after-hours, no trades will close that wouldn't have already.
- `monitor_positions` opens a separate `_get_conn()` for the SELECT and another for each UPDATE — fine for low volume but no transactional guarantees.
- `position_size` is stored as INR notional — but `pnl_pct` is computed using `(exit-entry)/entry`, so `pnl_inr = position_size × pnl_pct/100`. This means `pnl_inr` is the INR-on-the-notional, not on the position you actually held in shares. Consistent within the system but **not** a real-world rupee P&L.

---

## 9. `LearningAgent` — `agents/learning_agent.py`

**Purpose**: per-stock signal-weight EMA based on win/loss outcomes.

**Constants**:
- `WIN_BOOST = 1.05`, `LOSS_DECAY = 0.97`
- `MIN_WEIGHT = 0.1`, `MAX_WEIGHT = 3.0`
- `WEIGHT_SIGNALS = ["technical_score", "news_sentiment", "pattern_ev", "sector_momentum", "regime_alignment"]`

**Algorithm**:
- For each signal in the closed trade, if it was "positive" at entry (`technical_score > 5` or other > 0), multiply weight by 1.05 on win or 0.97 on loss; clip.

**Gotchas**:
- Weights are **read but never used** by `MasterAgent` in any meaningful way. They are part of the LLM prompt indirectly (via RAG `signal_weights` block) but the rule-based fallback ignores them entirely. Effectively a vestigial feature today.
- Signal `sector_momentum` is in `WEIGHT_SIGNALS` but **never set in `signals_at_entry`** by `main.py` — so it never updates.
- `weekly_analysis` reads from SQLite without filtering by date — it's actually "last 20 trades", not weekly.

> **✅ Fixed in `fix/verification-findings`** (CRIT-1 + CRIT-2). `main.py` now uses `core/row_utils.row_get` to read `sqlite3.Row` rows safely, the `trades` table has `technical_score / sentiment / pattern_ev / sector_momentum / regime_alignment / weights_applied` columns, and the loop applies each closed trade exactly once via `weights_applied=0` filter.

---

## 10. `DiscoveryAgent` — `agents/discovery_agent.py`

**Purpose**: scrape multiple sources, merge & rank, append to watchlist.

**Sources tried**:
1. NSE top gainers/losers (`live-analysis-variations`).
2. NSE volume gainers (`live-analysis-volume-gainers`).
3. NSE bulk deals (institutional buyer detection via name keywords).
4. MoneyControl most-active table (HTML scrape).
5. Twitter via Nitter (3 instances tried).
6. Google Trends (pytrends, optional import).

**Scoring**: each source contributes `score`, weights are fixed per source. Multi-source bonus: `score × 1.5` if appearing in ≥ 2 sources. Twitter sentiment adds `mentions × 0.1 + sentiment × 2` for the top-5 candidates.

**Side-effect**: `_add_to_watchlist(top_5_symbols)` rewrites `config.yaml` with `yaml.dump`. **Comments and original ordering are lost.**

**Gotchas**:
- All NSE endpoints are "soft-failed" with `logger.debug`. If everything fails you get a silent empty result.
- Nitter instances rotate frequently; expect this source to break often.

---

## 11. `PreOpenMonitor` — `agents/pre_open_monitor.py`

**Purpose**: catch gap-ups / gap-downs at 9:00 AM IST before market opens.

**Flow**:
1. Try `https://www.nseindia.com/api/market-data-pre-open?key=NIFTY` → all NIFTY 50 pre-open prices.
2. If empty: fall back to `_fetch_preopen_yfinance(watchlist)` (which actually computes "current vs prev close" — not really pre-open).
3. For each stock with `|gap_pct| ≥ 1.5%`, run `analyze_gap`:
   - Add catalysts: earnings filing, news sentiment, technical context (52w high, volume), historical gap-hold rate.
   - Score-based decision: BUY / WATCH / AVOID.

**Auto-add**: BUY signal stocks are auto-appended to watchlist (mutates `config.yaml`).

**Gotchas**:
- Fallback yfinance path is **misleadingly named**. It's really "did the close-to-close move beat 1.5%?" — not "is there a gap-up today?".
- `_compute_gap_history` only counts day-of holds; doesn't measure 3-day or full-day return.

---

## 12. `IntradayPatternScanner` — `agents/intraday_scanner.py`

> **✅ Fixed in `fix/verification-findings`** (HIGH-3). `agents/intraday_scanner.py` now defines `CANDLE_LOOKBACK = "2d"` and `CANDLE_INTERVAL = "5m"` at module top. Pattern detection runs end-to-end again.

**Purpose**: intraday pattern detection across NIFTY 50.

**Two-pass design**:
1. Groww batch LTP for all 50 (one API call, fast).
2. Filter by `|intraday_move| ≥ 1%` (or in watchlist).
3. Top 20 candidates → deep scan with 6 detectors.

**Detectors**:
| Detector | Confidence | Trigger |
|----------|-----------|---------|
| `bull_flag` | 75 | pole > +1.5%, flag range < 1.5%, breakout above pole high |
| `vwap_reclaim` | 70 | dipped below VWAP, reclaimed with 1.2× volume |
| `accumulation_at_support` | 65 | ≥3 touches of same low, rising volume |
| `volume_spike` | 60 | current vol ≥ 3× rolling, direction up |
| `resistance_breakout` | 80 | ≥2 prior rejections at level, break with 1.3× vol |
| `rsi_divergence` | 70 | bullish: price LL but RSI HL |

**BUY threshold**: best pattern confidence ≥ 65.

**Gotchas**:
- The "intraday move" gating uses `yfinance` daily Open vs current LTP — fine, but yet another network call per symbol.
- `time.sleep(0.3)` between deep scans is a hard-coded throttle (~6 s per scan of 20).

---

## 13. `EarningsCalendarAgent` — `agents/earnings_calendar_agent.py`

**Three modes** (selected via `context["mode"]`):
- `evening_prep` — flag stocks with earnings in next 3 days.
- `overnight_monitor` — poll NSE/BSE filings for the watchlist; score new ones; persist signal.
- `morning_scan` (default) — summarise overnight signals.

**Filing sources**:
- NSE: `/api/corp-announcements?index=equities&symbol=<SYM>`.
- BSE: `AnnSubCategoryGetData/w` (by company-name keyword match — fuzzy and lossy).

**Result scoring**: keyword counting (BEAT / MISS / INLINE) over `subject + content`. The actual report PDF is **not parsed** — only the filing subject line.

**Gotcha**: BSE keyword matching uses `company.split()[0].upper() in headline.upper()` — for "Reliance Industries Ltd" the keyword is "RELIANCE", which falsely matches any filing containing "Reliance".

---

## 14. ML model — `models/ml_model.py` (daily)

- Algorithm: `sklearn.GradientBoostingClassifier(n_estimators=300, max_depth=4, lr=0.05, subsample=0.8, max_features=0.8)`.
- Features: ~30 (returns 1/3/5/10/20d, EMA ratios, BB position/width, RSI 7/14/21, MACD hist, Stoch, ROC, volume ratio, OBV trend, VPT, hist vol 5/10/20, gap, intraday range, market context for nifty/banknifty/vix/4 sectors, day-of-week, month).
- Label: `1 if 5d forward return > 1.5%`.
- Validation: 5-fold `TimeSeriesSplit`. Final fit on full data.
- Output: `predict(symbol)` returns `{ml_signal (BUY/HOLD/SKIP), ml_proba, confidence}` with thresholds 0.55 / 0.40.

**Gotchas**:
- Trains on **all** stocks under `stocks/*/price_history.parquet` — including non-NSE stocks if you ran `fetch_universe.py`. That's intentional but undocumented.
- `predict()` re-loads market data for full date range every call — slow; should cache.

---

## 15. ML model — `models/india_intraday_model.py` (1h)

- Same algorithm, ~30 features tuned for 1h candles (hour-of-day, mins-to-close, intraday return from open, F&O expiry days/week/day flags, market context).
- Label: `1 if 3-hour forward return > 1.0%`.
- `dynamic_threshold(vix, regime, hour, fo_days)` — adjusts entry threshold ±0.03–0.08 based on conditions; clipped to [0.45, 0.80].
- CLI: `fetch | train | predict <SYM>`.

**Gotchas**:
- `_fo_expiry_days` computes "last Thursday of month". Doesn't account for **NSE holiday shifts** that move expiry to the previous trading day.
- `intraday_score` from `TechnicalAgent` (5m candles) is independent of `intraday_proba` from this model (1h candles). Two different "intraday" notions in the same prompt.

---

## 16. `ripple/` — sentiment subsystem

- `sentiment_analyzer.py` — wraps `ProsusAI/finbert` via HF pipeline. Uses BART (`facebook/bart-large-cnn`) to summarise text > 100 words before scoring.
- `twitter_collector.py` — actually fetches Reddit (stocks/wallstreetbets/investing) + Yahoo News, named "twitter" for legacy reasons.
- `pipeline.py` — `StockSentimentPipeline.run(symbol)` — aggregates and scores.
- `config.py` — `OUTPUT_DIR` defaults to `<repo_root>/output` (resolved relative to the package). `DEFAULT_MAX_TWEETS` is configurable via env var.

`NewsAgent` uses `SentimentAnalyzer` directly; the broader `StockSentimentPipeline` is not wired into the trading loop today.

---

## 17. `core/broker.py` — broker abstraction

- `Broker` ABC with `place_order/cancel_order/get_positions/get_order_status/get_ltp/brokerage/stt`.
- `PaperBroker` — yfinance-backed simulation with circuit-breaker (max 5 orders / 60s). **Not used by ExecutionAgent today** — ExecutionAgent talks to SQLite directly. The PaperBroker is reachable only via `get_broker(config)` and `__main__`.
- `ZerodhaBroker` — wraps `kiteconnect`. Requires `ZERODHA_API_KEY` and `ZERODHA_ACCESS_TOKEN` env vars; uses `MIS` (intraday) product.

This is a **latent capability** waiting to be wired in — see improvements doc.

---

## 18. `core/scheduler.py` — APScheduler daemon

`start()` builds a `BlockingScheduler(timezone="Asia/Kolkata")`. Job functions are top-level so they're picklable. `run_once()` runs the same jobs sequentially (useful for `--once` testing).

**Gotcha**: `job_intraday_scan` short-circuits if outside `09:15 ≤ now ≤ 15:00` — but **uses naive local time** (`datetime.now()`), not the scheduler's timezone. If you run on a non-IST machine, the gate is wrong.

---

## 19. `core/groww_client.py` — Groww REST client

- Auth flow: `SHA256(secret + timestamp)` checksum, then `POST /v1/token/api/access`. Tokens auto-refresh after 6h.
- Endpoints used: `/live-data/ltp`, `/live-data/quote`, `/live-data/ohlc` — all batch-friendly (50 symbols/call).
- Singleton via `get_groww_client()`.

Used only by `IntradayPatternScanner` today. Many other places (e.g. `ExecutionAgent._get_ltp`) still use yfinance.

---

## 20. `core/backtester.py` — event-driven backtester

- `Backtester.run(symbol, strategy, start, end, walk_forward_splits)`.
- Strategies: `RSIStrategy` (RSI cross above 30), `MACDStrategy` (bullish crossover).
- Slippage 5bps, brokerage 3bps each side, 20-bar timeout.
- Computes win-rate, EV, Sharpe (annualised √252), max drawdown.

Separate from `backtest_gap.py` and `backtest_intraday.py`, which are standalone scripts (not using this engine). **Three different backtesters in one repo.**

---

## 21. `simulate_day.py` — time-travel simulation

Picks the largest single-day move in the price history (or a date you provide) and replays the system as if it were live on T-1 evening. Synthesises an intraday path from daily OHLC (open → 30/60/80/90/95% of high → close). Useful for visualising the gap-fill SL strategy. Independent of the live system.

---

## 22. `test_stock.py` — full single-stock demo

Sequentially exercises every step (DataAgent → Earnings → PreOpen → News → Technical → Pattern → Regime → RiskManager → MasterAgent/LLM → ExecutionAgent → Backtester → Daily report) with verbose output. **The closest thing this repo has to an end-to-end test** — but it commits real artefacts (writes to KB, opens a paper trade) so it's not idempotent.
