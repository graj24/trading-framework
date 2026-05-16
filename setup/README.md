# Setup Guide

Everything you need to get the framework running. Start here.

---

## 1. Prerequisites

- Python 3.10+
- git
- ~2 GB free disk (stock data + ML models)

```bash
git clone <this-repo> trading-framework
cd trading-framework
python3.10 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

---

## 2. API keys

Copy the template and fill in what you need:

```bash
cp .env.example .env
```

### Required (nothing works without this)

| Key | Where to get it | Free? |
|-----|----------------|-------|
| `GROQ_API_KEY` | [console.groq.com](https://console.groq.com) → API Keys | Yes |

That's it. The system runs in paper-trading mode with just this one key.

### Recommended (better intraday signals)

| Key | Where to get it | What it unlocks |
|-----|----------------|-----------------|
| `GROWW_API_KEY` + `GROWW_SECRET` + `GROWW_TOTP_SECRET` | [groww.in/user/profile/trading-apis](https://groww.in/user/profile/trading-apis) | Live LTP, quotes, OHLC (replaces yfinance for intraday) |
| `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` | Create bot via [@BotFather](https://t.me/BotFather); get chat_id from `https://api.telegram.org/bot<TOKEN>/getUpdates` | Trade alerts, anomaly notifications |

### Only needed for live trading

| Key | Where to get it | What it unlocks |
|-----|----------------|-----------------|
| `ZERODHA_API_KEY` + `ZERODHA_API_SECRET` + `ZERODHA_ACCESS_TOKEN` | [kite.trade](https://kite.trade/docs/connect/v3/install/) | Real order placement via Zerodha |

To switch to live mode, also set `trading.mode: live` in `config.yaml`.

### Alternative LLM providers (optional)

The default is Groq (free). To use a different provider, set the key and update `llm.model` in `config.yaml`:

| Provider | Key | Example model string |
|----------|-----|---------------------|
| OpenAI | `OPENAI_API_KEY` | `openai/gpt-4o-mini` |
| Anthropic | `ANTHROPIC_API_KEY` | `anthropic/claude-3-5-sonnet-20241022` |
| AWS Bedrock | `AWS_ACCESS_KEY_ID` + `AWS_SECRET_ACCESS_KEY` + `AWS_REGION` | `bedrock/anthropic.claude-3-haiku` |

---

## 3. First run

```bash
# Build knowledge base for one stock (smoke test)
python -m agents.data_agent build RELIANCE

# Run one analysis cycle
python main.py

# Open the dashboard (read-only)
streamlit run ui/app.py
```

---

## 4. Build stock data

Before the ML models and pattern matching work, you need price history for your watchlist:

```bash
# All stocks in config.yaml watchlist (takes ~5–10 min)
python -m agents.data_agent build_all

# Or one at a time
python -m agents.data_agent build TCS
python -m agents.data_agent build HDFCBANK
```

---

## 5. Train the ML models (optional but recommended)

```bash
# Daily signal model (needs price history built first)
python ml_model.py train

# Intraday 1h model (needs 1h data fetched first)
python india_intraday_model.py fetch   # ~10 min, downloads 2y of 1h data
python india_intraday_model.py train
```

Models are saved to `stocks/ml_signal_model.pkl` and `stocks_1h/india_intraday_model.pkl`. The promotion gate prevents a worse model from overwriting a better one.

---

## 6. Run as a daemon (24/7 scheduler)

```bash
python -m core.scheduler
```

The scheduler follows IST market hours automatically:

| Time (IST) | What runs |
|-----------|-----------|
| 06:00 | Update knowledge bases |
| 07:00 | Discover new stocks |
| 08:30 | Pre-market technical analysis |
| 09:00 | Pre-open gap scan + signal generation |
| 09:15 | Execute trades |
| Every 5 min (09:15–15:00) | Monitor positions, intraday patterns |
| 15:00 | Close all open positions |
| 15:30 | Daily report + learning update |

---

## 7. Backtesting

```bash
# Gap strategy backtest
python -m core.backtester --strategy gap --threshold 2.0

# Intraday ML backtest (needs trained model)
python -m core.backtester --strategy ml_intraday --threshold 0.55

# Date-range replay (point-in-time simulation)
python -m core.replay --start 2025-01-01 --end 2025-03-31
```

---

## 8. Configuration

Edit `config.yaml` for runtime settings. Key knobs:

| Setting | Default | Effect |
|---------|---------|--------|
| `trading.mode` | `paper` | `paper` / `live` / `shadow` |
| `trading.broker` | `zerodha` | `zerodha` / `upstox` / `angelone` |
| `trading.capital` | `10000` | INR capital for position sizing |
| `risk.max_loss_per_day_pct` | `3.0` | Halts new trades for the day |
| `risk.max_open_positions` | `3` | Portfolio-wide cap |
| `llm.model` | `groq/llama-3.3-70b-versatile` | Any litellm model string |
| `watchlist` | 49 NIFTY 50 stocks | Stocks to analyse |

**Never edit `config.yaml` from code.** Dynamic additions go to `data/dynamic_watchlist.json`.

---

## 9. Shadow mode (test live broker without real money)

Shadow mode sends orders to both the paper ledger and your live broker simultaneously, then logs the fill difference. Useful for validating broker connectivity before going live.

```yaml
# config.yaml
trading:
  mode: shadow
  broker: zerodha   # or upstox / angelone
```

Requires the broker's API keys in `.env`. The paper leg always succeeds even if the live leg fails.

---

## 10. What you don't need

- No database server (SQLite only)
- No message queue
- No Docker (plain Python venv)
- No paid data feed (yfinance + Groww free tier covers everything)
- No Twitter/X API (discovery uses Nitter scraping)

---

## Troubleshooting

**`ModuleNotFoundError: transformers`** — FinBERT is heavy. Install it:
```bash
pip install transformers torch
```
Or set `news.use_finbert: false` in `config.yaml` to skip it.

**`GROQ_API_KEY not set`** — Copy `.env.example` to `.env` and fill in the key.

**`No price data for SYMBOL`** — Run `python -m agents.data_agent build SYMBOL` first.

**Telegram alerts not arriving** — Check `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` in `.env`. Send `/start` to your bot first.

**`kiteconnect not installed`** — Only needed for live Zerodha trading: `pip install kiteconnect`.

For more detail see [`docs/user-guide.md`](../docs/user-guide.md).
