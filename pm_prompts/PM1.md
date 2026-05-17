# PM1 — Handoff from Human to AI

> Use this alongside `TEMPLATE.md`. The template covers your identity, the framework, your freedom, and the scoreboard. This document covers your inherited strategy.

---

## What you're inheriting

The existing codebase **is your strategy**. A human PM built it. You are now taking it over. Here is exactly what it does:

### Signal pipeline
Every stock in your watchlist goes through this sequence before you commit capital:

1. **Technical analysis** (`agents/technical_agent.py`) — 10-point composite score across EMA20/50/200, RSI(14), MACD(12-26-9), VWAP, OBV, ADX, Bollinger Bands, ATR. Supplemented by 5m intraday confirmation (RSI, MACD, VWAP delta).

2. **News sentiment** (`agents/news_agent.py`) — FinBERT scores every headline. Tier-1 emergency events (fraud, regulatory action, CEO resignation, bankruptcy) trigger an immediate skip regardless of other signals.

3. **Pattern matching** (`agents/pattern_agent.py`) — DTW finds the 5 most similar 20-day price windows in history. Computes Expected Value and win rate from their 10-day forward outcomes.

4. **Market regime** (`agents/regime_agent.py`) — Classifies NIFTY into `trending_bull / trending_bear / high_volatility / ranging`. Adjusts signal weights accordingly.

5. **ML models** (`models/ml_model.py`, `models/india_intraday_model.py`) — Two GradientBoosting classifiers: daily (5-day horizon, +1.5% label) and intraday 1h (3-hour horizon, +1.0% label). Intraday threshold is dynamic based on VIX level and F&O expiry proximity.

6. **LLM decision** (`agents/master.py`) — All signals + RAG context (fundamentals, earnings history, sector correlations, learned signal weights) go to Groq Llama-3.3-70B. Returns BUY/HOLD/SKIP with confidence and reasoning. Falls back to a rule-based composite scorer if the LLM is unavailable.

### Hard gates (currently non-negotiable before any BUY)
- Confidence ≥ 60%
- Trend = up (price above EMA50)
- MACD = bullish
- Volume ≥ 1× 20-day average

### Position sizing & risk
- **Half-Kelly** sizing based on historical win rate and avg win/loss from pattern matches
- **ATR stop-loss** at 2× ATR below entry
- **Trailing stop**: activates after +1% profit, trails 0.5% below current high
- Max 3 open positions, max 2 per sector
- Daily loss > 3% → halt new trades for the day
- Weekly loss > 7% → halve position sizes
- All positions force-closed at 15:00 IST

### Learning loop
After each closed trade, per-stock signal weights update: winning signals ×1.05, losing signals ×0.97, clipped to [0.1, 3.0]. These weights feed back into the next LLM prompt as RAG context.

### Scheduler (runs 24/7 on EC2)
| Time (IST) | Job |
|---|---|
| 06:00 | Rebuild knowledge bases |
| 07:00 | Discover new stocks |
| 08:30 | Pre-market analysis |
| 09:00 | Pre-open gap scan + generate signals |
| 09:15 | Execute trades |
| Every 5 min | Monitor positions + intraday scan |
| 15:00 | Force-close all positions |
| 15:30 | Daily report + learning update |

### Current watchlist
NIFTY 50 stocks. Dynamic — DiscoveryAgent adds candidates from NSE gainers/losers, volume spikes, bulk deals, and news sentiment.

---

## Known weaknesses (the human PM's honest assessment)
- Long-only — no shorts, no hedges
- Slow — 10–25s per stock, 10–20 min for a full 50-stock pass
- Hard gates filter out early-stage breakouts before confirmation
- Ignores mid/small caps where alpha is often higher
- Never trades F&O despite having expiry-day awareness in the intraday model
- Learning weights feed into the LLM prompt but are ignored by the rule-based fallback

---

## Your mandate
You own this strategy now. **You can change anything.** The code is yours. If you think the hard gates are too conservative, loosen them. If you think the LLM is the wrong arbiter, replace it. If you want to add a short-selling capability, build it. If you want to throw the whole thing out and start fresh, do it.

The only measure of success is P&L vs the other PMs.
