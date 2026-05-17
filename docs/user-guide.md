# User Guide — Autonomous Trading Framework

> Audience: anyone who wants to install, configure, and run the system. No prior knowledge of the codebase assumed.
> If you want to extend or contribute, also read `technical-reference.md`.

---

## Contents
1. [What this is](#1-what-this-is)
2. [What you need](#2-what-you-need)
3. [Quick start (10 minutes)](#3-quick-start-10-minutes)
4. [Configuration](#4-configuration)
5. [Common workflows](#5-common-workflows)
6. [The dashboard](#6-the-dashboard)
7. [Backtesting](#7-backtesting)
8. [Machine learning models](#8-machine-learning-models)
9. [Live trading](#9-live-trading)
10. [Troubleshooting](#10-troubleshooting)
11. [FAQ](#11-faq)
12. [Glossary](#12-glossary)

---

## 1. What this is

**Autonomous Trading Framework** is a tool that:
- Watches a basket of Indian (NSE) stocks.
- Combines **technical indicators**, **news sentiment** (FinBERT), **historical pattern matching**, **market regime**, and **two machine-learning models** into a single trade decision.
- Asks an **LLM** (Groq Llama-3.3-70B by default) to make the final call, with a deterministic rule-based fallback when the LLM is unavailable.
- **Paper-trades** by default — every trade is recorded in a local SQLite database (`paper_trades.db`).
- Automatically **manages risk** (Kelly sizing, ATR stop-losses, trailing stops, daily/weekly loss limits, sector concentration).
- Comes with a **Streamlit dashboard** to inspect signals, news, and backtests.
- Can run as a **24/7 daemon** that follows the IST market schedule (06:00 → 15:45) and monitors positions every 5 minutes.

It is **not** financial advice. It is a research and learning tool. Use paper trading until you have evaluated the system's behaviour for at least one full month of live market data on your watchlist.

---

## 2. What you need

### 2.1. Hardware / OS
- macOS, Linux, or Windows (WSL2).
- 4 GB RAM minimum (8 GB recommended once FinBERT is loaded).
- ~2 GB free disk for stock data + ML models.
- Stable internet connection (most agents call external APIs).

### 2.2. Software
- **Python 3.10+** (the `pyproject.toml` declares `>=3.10`).
- `git` to clone the repo.
- `pip` (or `uv`, if you prefer).

### 2.3. Accounts (all optional except the LLM)
| Service     | What for                    | Required?                    | Free tier? |
|-------------|-----------------------------|------------------------------|------------|
| Groq        | LLM completions             | Yes (or another litellm provider) | Yes |
| Groww       | Live LTP / quote / OHLC     | Recommended for intraday     | Yes (with API access) |
| Telegram    | Trade alerts                | Optional                     | Yes |
| Zerodha     | **Live** order placement    | Only if `mode: live`         | Demat fees |
| Twitter     | Reserved (current code uses Nitter scraping, no key needed) | No | — |

The system **will run** without any of the optional accounts — discovery and monitoring agents simply have fewer signal sources.

---

## 3. Quick start (10 minutes)

### 3.1. Clone and install

```bash
git clone <your-fork-or-this-repo> trading-framework
cd trading-framework

python3.10 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 3.2. Configure secrets

Create `.env` at the repo root. **Do not commit it.** Minimum to run:

```bash
# .env
GROQ_API_KEY=your_groq_key_here          # for the LLM (free tier)
# or use any litellm-supported provider; see https://docs.litellm.ai
```

If you want live data via Groww (recommended for intraday):

```bash
GROWW_API_KEY=...
GROWW_SECRET=...
GROWW_ACCESS_TOKEN=...
GROWW_TOTP_SECRET=...
```

If you want Telegram alerts:

```bash
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
```

### 3.3. Build per-stock data

```bash
# Build the knowledge base for a single stock first (sanity check)
python -m agents.data_agent build RELIANCE

# When that works, build the whole watchlist (takes ~5 minutes)
python -c "
import yaml
from agents.data_agent import DataAgent
with open('config.yaml') as f: cfg = yaml.safe_load(f)
da = DataAgent(cfg)
for s in cfg['watchlist']: da.build_kb(s)
"
```

### 3.4. Run a single analysis cycle

```bash
python main.py
```

You should see, for each watchlist symbol:
```
RELIANCE: HOLD (conf=42%) — Composite 42/100 but filters: trend=sideways
TCS:      BUY  (conf=72%) — ...
...
```

If a `BUY` is generated and confidence ≥ 60, the system opens a paper trade in `paper_trades.db`.

### 3.5. Inspect the result in the dashboard

```bash
pip install streamlit plotly      # if not already installed
streamlit run scripts/dashboard.py
```

Open http://localhost:8501. You'll see five tabs: Portfolio, Signals, Backtest, News, Intraday ML.

---

## 4. Configuration

All non-secret configuration lives in `config.yaml`. Edit and re-run — most agents read it on each call.

### 4.1. The five things you most likely want to change

```yaml
# config.yaml

trading:
  mode: paper                 # 'paper' or 'live' (live is gated — see §9)
  capital: 10000              # INR; affects position sizing
  currency: INR

watchlist:                    # NSE root symbols (no '.NS')
  - RELIANCE
  - TCS
  - HDFCBANK
  # ... add or remove freely

core_watchlist:               # Never pruned by the auto-trimmer
  - RELIANCE
  - INFY

watchlist_max: 20             # Hard cap; auto-prune trims to this size

llm:
  model: groq/llama-3.3-70b-versatile   # Any litellm model id
  temperature: 0.1
  max_tokens: 2000
```

### 4.2. Risk knobs (turn carefully)

```yaml
risk:
  kelly_fraction: 0.5                # 0.5 = half-Kelly; 1.0 = full (aggressive)
  max_loss_per_trade_pct: 1.0        # Risk cap per trade
  max_loss_per_day_pct: 3.0          # Halts new trades for the day if hit
  max_loss_per_week_pct: 7.0         # Halves position sizes if hit
  max_loss_per_month_pct: 15.0       # Logged warning
  max_open_positions: 3              # Across the entire portfolio
  trailing_stop_trigger_pct: 1.0     # Profit % at which trailing stop activates
  trailing_stop_distance_pct: 0.5    # How far below current price to trail
  close_all_time: '15:00'            # IST market-close cutoff
```

### 4.3. Schedule

```yaml
schedule:
  pre_market_data: '06:00'        # KB refresh
  pre_market_analysis: '08:30'
  market_open_signals: '09:00'    # First signals
  market_open_execute: '09:15'    # Execute paper trades
  intraday_interval_minutes: 5    # Position monitor + intraday scan cadence
  post_market: '15:30'            # Reports + learning update
```

These keys are read by `core/scheduler.py`. The scheduler hard-codes corresponding cron triggers — changing the YAML alone does **not** reschedule jobs (you need to also edit the `CronTrigger(hour=…, minute=…)` calls). For now, treat `config.yaml:schedule` as documentation.

---

## 5. Common workflows

### 5.1. Run once, manually

```bash
python main.py                    # default: one cycle over the watchlist
```

What it does:
1. Loads config and `.env`.
2. Calls `MasterAgent.run_for_stock(sym)` for each watchlist entry.
3. If `BUY` and you don't already have a position, opens a paper trade.
4. Walks all open positions, prints unrealised P&L.
5. Walks all closed trades, updates `signal_weights.json` per stock.

### 5.2. Run all scheduler jobs once (smoke test)

```bash
python main.py --once
```

Runs the entire morning sequence (KB update → discovery → pre-open → analysis → signals → execute → monitor → post-market) one time, then exits. Good for daily testing without being market-hours-bound.

### 5.3. Run as a 24/7 daemon

```bash
python main.py --schedule
```

Starts APScheduler in `Asia/Kolkata` timezone. Press `Ctrl+C` to stop.

To run unattended on a server:

```bash
# Linux/macOS — nohup
nohup python main.py --schedule > logs/scheduler.out 2>&1 &
```

```bash
# Or use a systemd service (recommended for production)
# /etc/systemd/system/trading-framework.service
[Unit]
Description=Autonomous Trading Framework
After=network.target

[Service]
Type=simple
WorkingDirectory=/path/to/trading-framework
EnvironmentFile=/path/to/trading-framework/.env
ExecStart=/path/to/trading-framework/.venv/bin/python main.py --schedule
Restart=on-failure
User=trading

[Install]
WantedBy=multi-user.target
```

### 5.4. Analyse a single stock end-to-end

```bash
python test_stock.py RELIANCE
```

This walks every step (DataAgent → Earnings → PreOpen → News → Technical → Pattern → Regime → Risk → LLM → Execute → Backtest → Daily report) with verbose output. **Note**: it writes to the KB and may open a paper trade.

### 5.5. Time-travel a historical big-move day

```bash
python simulate_day.py TATACONSUM            # auto-picks largest move
python simulate_day.py TATACONSUM 2024-01-12 # specific date
```

Shows what the system would have decided on T-1 evening, the pre-open gap signal at 09:00, the paper trade simulation through the day, and the final P&L. Useful for visualising the gap-fill SL strategy.

### 5.6. Build a larger universe

```bash
python fetch_universe.py nifty       # NIFTY 50
python fetch_universe.py sp500       # 50 large-cap US (sample)
python fetch_universe.py all         # everything (~250 symbols)
```

After fetching, retrain the daily ML model so it learns from the broader history:

```bash
python models/ml_model.py train
```

---

## 6. The dashboard

```bash
streamlit run scripts/dashboard.py
```

### 6.1. Tabs

| Tab          | What you see                                                                  |
|--------------|-------------------------------------------------------------------------------|
| 💼 Portfolio | Open positions with live LTPs, unrealised P&L, candlestick charts, closed trades, cumulative P&L |
| 🎯 Signals   | Today's signals per watchlist stock + per-stock learned signal weights        |
| 📊 Backtest  | Interactive gap-strategy backtest — adjust threshold slider                    |
| 📰 News      | Per-stock news history, sentiment distribution, articles table                |
| ⚡ Intraday ML | India intraday model backtest (1h GradientBoosting) — adjust threshold/SL/T  |

The dashboard is **read-only** — it does not place trades.

### 6.2. Refresh

LTPs are cached for 60 seconds (`@st.cache_data(ttl=60)`). Use the sidebar **🔄 Refresh** button to clear caches.

### 6.3. Common surprises

- **Empty portfolio tab** = `paper_trades.db` is empty. Run `python main.py` first.
- **"No news stored"** in News tab = `NewsAgent` hasn't run for that symbol. Run `python main.py` once.
- **"Intraday model not trained yet"** = `models/stocks_1h/india_intraday_model.pkl` missing. Run `python models/india_intraday_model.py fetch && python models/india_intraday_model.py train`.

---

## 7. Backtesting

There are three backtesters in this repo (a known overlap — see `analysis/05-issues.md` C1):

### 7.1. Built-in event-driven backtester (RSI / MACD)

```bash
python -m core.backtester --stock RELIANCE --strategy rsi
python -m core.backtester --stock RELIANCE --strategy macd --start 2020-01-01 --walk-forward 3
```

Output:
```
=========================================================
  Backtest: RELIANCE | Strategy: RSIStrategy
=========================================================
  Trades:        38
  Win Rate:      52.6%
  Avg Gain:      +2.41%
  Avg Loss:      -1.83%
  Expected Val:  +0.40%
  Sharpe Ratio:  0.84
  Max Drawdown:  -8.2%
  Total Return:  +15.30%
```

### 7.2. Gap-strategy backtest

```bash
python backtest_gap.py 2.0    # gap threshold = 2%
```

Tests every day in your watchlist's history where the open gapped up ≥ threshold% above the prior close, with filters (volume > 1.5×, price > EMA50, MACD bullish).

### 7.3. Intraday ML backtest

```bash
python backtest_intraday.py              # all 1h-trained stocks
python backtest_intraday.py TATACONSUM   # single stock
```

Walks every 1h candle since training cutoff; enters when `proba ≥ 0.55`; exits on trailing stop / target / EOD.

### 7.4. Comparing the three

The slippage and brokerage assumptions differ slightly between scripts (5–10 bps each side). Treat backtest numbers as **directional** — the same strategy will produce slightly different headline numbers across the three. The `dashboard.py:Tab 3` reproduces the gap-strategy logic in a fourth, interactive form.

---

## 8. Machine learning models

There are two ML models, both `sklearn.GradientBoostingClassifier`:

| Model                         | Cadence | Forward horizon | Threshold | Output    |
|-------------------------------|---------|-----------------|-----------|-----------|
| `ml_model.py`                 | Daily   | 5 days          | +1.5%     | BUY/HOLD/SKIP |
| `india_intraday_model.py`     | 1 hour  | 3 hours         | +1.0%     | BUY/HOLD/SKIP (with dynamic threshold) |

### 8.1. Train

```bash
# Daily model — uses every stock in stocks/*/price_history.parquet
python models/ml_model.py train

# Intraday model — fetch first if you haven't
python models/india_intraday_model.py fetch
python models/india_intraday_model.py train
```

Training prints CV AUC per fold and feature importances. Models are saved to:
- `stocks/ml_signal_model.pkl`
- `models/stocks_1h/india_intraday_model.pkl`

### 8.2. Predict

```bash
python models/ml_model.py predict TCS
python models/india_intraday_model.py predict TCS
```

These are also automatically used inside `MasterAgent.run_for_stock` — explicit invocation is for debugging.

### 8.3. Backtest the ML signals

```bash
python models/ml_model.py backtest                # across watchlist
python models/ml_model.py backtest RELIANCE       # single
```

Reports BUY-signal accuracy = "of all days the model said BUY, what fraction had the 5-day forward return > 1.5%?"

### 8.4. Retraining cadence

There is no automatic retraining today. We recommend retraining:
- The **daily model** monthly.
- The **intraday model** weekly (or after re-fetching with `fetch`).
- After any major market regime shift (e.g. a budget event, election week).

---

## 9. Live trading

> ⚠️ **Live trading is partially wired today.** The `Broker` abstraction supports Zerodha Kite, but `ExecutionAgent` writes directly to SQLite. Setting `mode: live` raises `RuntimeError("Live trading not yet enabled. Set mode=paper in config.")`.

### 9.1. What works

- `core/broker.py:ZerodhaBroker` — full implementation: place/cancel orders, get positions, LTP.
- `core/broker.py:get_broker(config)` — factory by `mode`.

### 9.2. What is needed before going live

You (or a maintainer) must:
1. Add `ZERODHA_API_KEY` and `ZERODHA_ACCESS_TOKEN` to `.env`.
2. Modify `agents/execution_agent.py:execute_trade` to call `get_broker(self.config).place_order(...)` and reconcile the order with SQLite.
3. Run a **shadow period** (paper + live in parallel) to verify slippage/brokerage assumptions.
4. Reduce `trading.capital` to a safe amount (e.g. ₹2000) for the first live week.

This work is in the roadmap (`analysis/06-improvements.md` P1 §13).

### 9.3. Risk controls in live mode

When live mode is enabled, the existing risk controls still apply:
- Daily loss > 3% halts new trades.
- All positions are force-closed at 15:00 IST.
- Per-trade Kelly fraction is `0.5` (half-Kelly).
- Max 3 concurrent positions, max 2 per sector.

You should **also** consider adding broker-side limits (Zerodha allows order-value caps and position limits via the dashboard).

---

## 10. Troubleshooting

### 10.1. `No price data for SYMBOL`
- The symbol is missing from `stocks/`. Run `python -m agents.data_agent build SYMBOL`.

### 10.2. `Insufficient data for SYMBOL: N rows`
- Need ≥200 daily bars. Either the stock is too new, or yfinance returned partial data. Re-run the build.

### 10.3. `LLM unavailable, using rule-based fallback`
- `GROQ_API_KEY` missing or invalid.
- Rate-limited.
- Network unreachable.
- The fallback is correct and conservative; the system continues to function.

### 10.4. Empty news feed
- yfinance news endpoint blocked or rate-limited.
- The other news sources in `news_agent.py` (`_scrape_moneycontrol`, etc.) are stubs that return `[]`.
- Try `python -m agents.news_agent` to confirm whether yfinance is reachable.

### 10.5. `Intraday model not trained yet`
- `models/stocks_1h/india_intraday_model.pkl` doesn't exist.
- Run `python models/india_intraday_model.py fetch && python models/india_intraday_model.py train` (~10 minutes).

### 10.6. Streamlit shows numbers in red
- That's intentional — losses display in red, gains in green. Not an error.

### 10.7. APScheduler missed jobs
- Daemon was paused / asleep. APScheduler logs `Run time of job ... was missed by 0:01:23.456789` — usually safe to ignore for cron-style jobs (pre-market analysis can run a few minutes late).
- For interval jobs (monitor_positions), missed runs simply skip.

### 10.8. SQLite "database is locked"
- Two processes are writing simultaneously (e.g. a `main.py` and `--schedule` running in parallel). Stop one.

### 10.9. yfinance / NSE rate limits
- Pause for a minute and retry. Yfinance can return empty `DataFrame` silently — check log lines like `"Building knowledge base for SYM"` followed by no row count.

### 10.10. ✅ FIXED — `AttributeError: 'sqlite3.Row' object has no attribute 'get'`
Resolved in `fix/verification-findings` (CRIT-1). If you ever see this on an older clone, `git pull` and re-run.

### 10.11. ✅ FIXED — `IntradayPatternScanner` returns no signals during market hours
Resolved in `fix/verification-findings` (HIGH-3). The undefined `CANDLE_LOOKBACK` / `CANDLE_INTERVAL` globals are now defined; pattern detection runs again.

---

## 11. FAQ

**Q. How much capital should I start with?**
A. Whatever you can afford to lose. The default is ₹10,000 in paper. The system has been tested only at this scale; large-cap stock prices (₹2k–₹5k each) mean position sizes of 1–3 shares — be aware that the EV math behind patterns/ML assumes continuous sizing.

**Q. Why does it only trade NIFTY 50 stocks?**
A. The 1h ML model is trained on the NIFTY 50 universe; the discovery and monitoring agents target NSE-only endpoints. You can extend `watchlist` to mid-caps that have ≥3 years of yfinance data, but the intraday ML signal will be weaker for unseen symbols.

**Q. Does it short stocks?**
A. No. The system is long-only. There is no SELL handler in `main.py` or the scheduler — only BUY/HOLD/SKIP.

**Q. Can I use a different LLM?**
A. Yes. Set `llm.model` to any string `litellm` understands — `openai/gpt-4o-mini`, `anthropic/claude-3-5-sonnet-20241022`, `bedrock/anthropic.claude-3-haiku`, etc. Set the corresponding API key in `.env`.

**Q. How does the system "learn"?**
A. After a paper trade closes, `LearningAgent.update_weights` adjusts per-stock signal weights — multiplied by 1.05 on win or 0.97 on loss, clipped to [0.1, 3.0]. These weights are passed in the LLM prompt as RAG context. *(Caveat: in the rule-based fallback they are not used — see `analysis/05-issues.md` B3.)*

**Q. Why are there so many open NSE/BSE/MoneyControl scraper paths?**
A. Robustness. NSE breaks endpoints frequently and adversarially blocks scrapers. The system tries multiple paths and silently degrades. If everything fails, it just produces fewer signals — it doesn't crash.

**Q. What happens if I run the daemon but the market is closed (weekend, holiday)?**
A. APScheduler still fires cron jobs at the configured times. Most agents handle empty/stale data gracefully (return empty results, log warnings). Position monitoring on a closed market is a no-op.

**Q. Where do my trades live?**
A. `paper_trades.db` (SQLite) at the repo root. Inspect with `sqlite3 paper_trades.db "SELECT * FROM trades;"` or use the dashboard.

**Q. The dashboard shows trades I never made.**
A. Probably from `test_stock.py` or `python -m agents.execution_agent` smoke tests — both insert real rows. Clean up with:
```bash
sqlite3 paper_trades.db "DELETE FROM trades WHERE reasoning='Test trade';"
```

**Q. How do I change which symbols are analysed without losing my edits?**
A. Today: edit `config.yaml:watchlist` directly. Caveat: `DiscoveryAgent` and `PreOpenMonitor` may auto-append symbols. To stop that, comment out `_add_to_watchlist` calls in those agents, or set `watchlist_max` to your desired count.

**Q. Is this safe for production?**
A. As paper-trading research: yes. As a live-money trading system: not yet. See `analysis/05-issues.md` for the open list and `analysis/06-improvements.md` for the roadmap.

**Q. Does it consider F&O / options?**
A. Cash equities only. The intraday ML uses **F&O expiry-day flags** as features, but the system does not place options orders.

---

## 12. Glossary

| Term            | Meaning                                                                            |
|-----------------|------------------------------------------------------------------------------------|
| **NSE**         | National Stock Exchange of India.                                                  |
| **NIFTY 50**    | Index of the 50 largest NSE stocks. The default watchlist.                         |
| **BSE**         | Bombay Stock Exchange of India. Used as a backup for filings.                      |
| **LTP**         | Last Traded Price.                                                                 |
| **VWAP**        | Volume-Weighted Average Price (intraday benchmark).                                |
| **OHLCV**       | Open, High, Low, Close, Volume — the canonical bar.                                |
| **EMA**         | Exponential Moving Average. We use 20 / 50 / 200 periods.                          |
| **RSI**         | Relative Strength Index. >70 = overbought, <30 = oversold.                         |
| **MACD**        | Moving Average Convergence Divergence. We use 12-26-9 standard.                    |
| **ADX**         | Average Directional Index. >25 = strong trend.                                     |
| **ATR**         | Average True Range — daily volatility measure.                                     |
| **VIX (India)** | NSE's volatility index. >20 = elevated fear.                                       |
| **F&O**         | Futures & Options. Monthly expiry on the last Thursday.                            |
| **Tier 1 news** | Emergency-level events (fraud, CEO resign, regulatory action). Triggers auto-skip. |
| **Kelly**       | Optimal bet-sizing formula. We use **half-Kelly** (50% of full).                   |
| **DTW**         | Dynamic Time Warping — similarity measure for time series.                         |
| **EV**          | Expected Value — probability-weighted average outcome.                             |
| **AUC**         | Area Under the ROC Curve. Reported per cross-validation fold for ML models.        |
| **Slippage**    | Difference between expected and actual fill price (we model 5 bps each side).      |
| **Brokerage**   | Per-side fee (we model 3 bps).                                                     |
| **STT**         | Securities Transaction Tax (India: 10 bps on sell side).                           |
| **MIS**         | Margin Intraday Square-off — Zerodha's intraday product type.                      |
| **Paper trade** | Simulated trade with no real money. Recorded in `paper_trades.db`.                 |

---

## Need more detail?

- **Architecture and modules**: `docs/technical-reference.md`.
- **Why something is the way it is**: `docs/analysis/01-architecture.md` and friends.
- **Known bugs / smells**: `docs/analysis/05-issues.md`.
- **Roadmap**: `docs/analysis/06-improvements.md`.

If you find a bug or want to propose a feature, open an issue on the repo with:
- Output of `python main.py` showing the failure.
- Relevant lines from `logs/trading.log`.
- Your `config.yaml` (with secrets removed).
