# What This Framework Needs From You

This is the complete list. Nothing else is required.

---

## The only thing you must have

### Groq API Key (free, 2 minutes)
The LLM brain of the framework. Every trade decision goes through it.

1. Go to [console.groq.com](https://console.groq.com)
2. Sign up (free, no credit card)
3. Create an API key
4. Paste it in your `.env` file:
   ```
   GROQ_API_KEY=gsk_...
   ```

That's it. The framework runs fully in paper-trading mode with just this.

---

## Things that make it better (all free)

### Groww API — live stock prices
Without this, prices come from yfinance (15-min delayed). With it, you get real-time LTP.

1. Log in to [groww.in](https://groww.in)
2. Go to Profile → Trading APIs
3. Generate API key + secret
4. Set up TOTP (Google Authenticator)
5. Add to `.env`:
   ```
   GROWW_API_KEY=...
   GROWW_SECRET=...
   GROWW_TOTP_SECRET=...
   GROWW_ACCESS_TOKEN=...   # refresh daily
   ```

### Telegram Bot — trade alerts
Get notified when a trade is placed, a stop-loss is hit, or something anomalous happens.

1. Open Telegram, search for **@BotFather**
2. Send `/newbot`, follow the prompts → you get a token
3. Start a chat with your new bot (send any message)
4. Visit `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates` → find your `chat_id`
5. Add to `.env`:
   ```
   TELEGRAM_BOT_TOKEN=...
   TELEGRAM_CHAT_ID=...
   ```

---

## Only if you want real money trading

### Zerodha Kite (default live broker)
1. Open a Zerodha demat account at [zerodha.com](https://zerodha.com)
2. Subscribe to Kite Connect API at [kite.trade](https://kite.trade) (₹2000/month)
3. Create an app → get API key + secret
4. Generate a daily access token (or automate it)
5. Add to `.env`:
   ```
   ZERODHA_API_KEY=...
   ZERODHA_API_SECRET=...
   ZERODHA_ACCESS_TOKEN=...   # refresh daily
   ```
6. In `config.yaml`: set `trading.mode: live` and `trading.broker: zerodha`

### Upstox (alternative)
1. Open account at [upstox.com](https://upstox.com)
2. Register as developer at [developer.upstox.com](https://developer.upstox.com)
3. Add to `.env`:
   ```
   UPSTOX_API_KEY=...
   UPSTOX_ACCESS_TOKEN=...
   ```
4. In `config.yaml`: set `trading.broker: upstox`

### Angel One SmartAPI (alternative)
1. Open account at [angelone.in](https://www.angelone.in)
2. Register at [smartapi.angelbroking.com](https://smartapi.angelbroking.com)
3. Add to `.env`:
   ```
   ANGELONE_API_KEY=...
   ANGELONE_CLIENT_ID=...
   ANGELONE_PASSWORD=...
   ANGELONE_TOTP_SECRET=...
   ```
4. In `config.yaml`: set `trading.broker: angelone`

---

## Alternative LLM providers (if you don't want Groq)

| Provider | Key name | Free tier | Model example |
|----------|----------|-----------|---------------|
| OpenAI | `OPENAI_API_KEY` | No (pay-per-use) | `openai/gpt-4o-mini` |
| Anthropic | `ANTHROPIC_API_KEY` | No (pay-per-use) | `anthropic/claude-3-5-sonnet-20241022` |
| AWS Bedrock | `AWS_ACCESS_KEY_ID` + `AWS_SECRET_ACCESS_KEY` + `AWS_REGION` | Free tier available | `bedrock/anthropic.claude-3-haiku` |

Change `llm.model` in `config.yaml` to switch.

---

## What you do NOT need

| Thing | Why not needed |
|-------|---------------|
| Database server | Uses local SQLite (`paper_trades.db`) |
| Docker / Kubernetes | Plain Python venv |
| Paid data feed | yfinance (free) + Groww free tier covers everything |
| Twitter/X API | Discovery uses public Nitter scraping |
| GPU | ML models are gradient boosting (CPU only) |
| Cloud server | Runs on your laptop |

---

## Summary checklist

```
[required]
☐ Python 3.10+
☐ GROQ_API_KEY

[recommended — free]
☐ GROWW_API_KEY + GROWW_SECRET + GROWW_TOTP_SECRET
☐ TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID

[only for live trading]
☐ Broker API keys (Zerodha / Upstox / Angel One)
☐ config.yaml: trading.mode = live
```
