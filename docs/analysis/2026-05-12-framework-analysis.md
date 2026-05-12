# Framework Analysis — 2026-05-12

**Context:** First thorough analysis of the trading framework after the FinBERT sentiment + RAG commits were merged. Goal of the exercise: identify what would most increase success rate (defined by user as *maximizing absolute returns*). Starting from scratch — no baseline paper-trading metrics yet.

## Overall Assessment

The architecture is solid — probably the best-designed personal trading framework I've seen. Agent separation is clean, there's real rigor (Kelly sizing, ATR stops, risk limits, regime awareness, RAG, learning loop), and the data layer is well-thought-out. This is not a toy.

But there's a significant gap between *framework sophistication* and *actual predictive edge*. Most of what determines success rate lives in inputs and execution that this framework doesn't yet cover.

## Strengths

- **Architecture.** Clear single-responsibility agents, proper abstractions (`Agent`, `AgentResult`, knowledge base layer).
- **Risk management.** Kelly sizing, ATR-based stops (already in `risk_manager.py`), daily/weekly/monthly loss limits, correlation + sector concentration checks. This alone puts you ahead of most retail traders.
- **Regime awareness.** Different scoring weights per market regime is the right instinct.
- **Feedback loop.** `LearningAgent` adjusts signal weights from actual outcomes.
- **Per-stock knowledge base.** `event_reactions`, `sector_correlation`, `signal_weights` — this is institutional thinking.
- **Hard filter gate.** Trend + MACD + volume gate for BUY signals, backtest-validated.

## Weaknesses (limit success rate today)

1. **Data monoculture.** Everything depends on yfinance. For Indian markets, yfinance is delayed, flaky, and rate-limited. The 5-min "intraday" is actually delayed data — trading on stale prices.
2. **Scraping stubs are empty.** `_scrape_moneycontrol`, `_scrape_economic_times`, `_scrape_nse_announcements` all return `[]`. NewsAgent effectively only reads Yahoo, which is sparse for NSE stocks.
3. **ATR SL exists but isn't used.** `risk_manager.atr_stop_loss()` is implemented but `master.py` uses `price * 0.99`. The good code is unreached.
4. **Fixed 1% / 2.5% SL/Target on daily trades.** On Indian large caps with ~1.5% daily ATR, this is barely outside noise. Targets rarely hit before stops.
5. **Only long, only equity cash.** In bear/ranging regimes (which the framework explicitly detects) you literally can't profit. No shorts, no pair trades, no options. Half the year you're sidelined.
6. **Learning agent is too coarse.** Weights update globally, not per-regime. "Technical score worked in a bull market" gets generalized to "technical score always works."
7. **Pattern agent is mostly decorative.** DTW on 20-day price windows searching for 10-day forward returns with 5 matches — a very weak statistical signal. Not a bad idea, but EV from 5 matches isn't significant.
8. **No walk-forward / out-of-sample validation** visible in the gap backtester. Risk of curve-fit filters.
9. **No transaction cost realism.** Slippage 0.05% + brokerage 0.03% ignores STT, exchange charges, stamp duty, GST. Real Indian round-trip cost on intraday is ~0.1–0.15%, delivery ~0.15–0.2%. At ₹10K capital this matters a lot.
10. **9-stock watchlist.** Selection bias. DiscoveryAgent exists but uses Google Trends + volume, not fundamental or flow-based filtering.
11. **No market microstructure.** No bid-ask, no FII/DII flows, no options OI, no PCR, no delivery %. These are the actual leading indicators in Indian markets.
12. **Capital ₹10K is below effective minimum.** Kelly-sized positions × 3-position limit → absolute P&L so small that fixed charges eat most of it.

## What's Missing — High-Leverage Additions

Rough priority order of expected impact on success rate:

1. **Real broker data feed** — replace yfinance with Kite Connect or Groww API (client stubs exist). Real-time L1 quotes, reliable OHLCV, options chain.
2. **Options data layer** — OI changes, PCR, max pain. Indian markets: F&O volume dwarfs cash.
3. **FII/DII daily flows** — scrape NSDL/NSE. Single best leading indicator for index direction.
4. **Wire ATR-based SL + structure-based targets** (already noted in `plans/IMPROVEMENTS.md`).
5. **Regime-specific strategy routing** — trend-following for `trending_bull`, mean-reversion for `ranging`, vol strategies for `high_volatility`. Today it's one strategy with tweaked weights.
6. **Walk-forward validation** on the gap backtester and all rule thresholds.
7. **(Regime × signal) weight matrix** in learning agent — not global weights.
8. **Shortable universe** via futures, even if paper.
9. **Real news depth** — implement MoneyControl/ET scraping, or use a news API (FinNhub, Alpha Vantage, NewsAPI).
10. **Better pattern recognition** — learned from price+volume+regime features, not raw-price DTW on 5 matches.

## Key Insight

The framework is *ready for more signal*, but currently *starved of it*. Adding another rule or another LLM prompt won't help. The bottleneck is information quality and execution realism, not decision logic.

## Open Questions (to resolve next)

- What infrastructure is available (broker API access, paid data feeds, compute)?
- What's the eventual capital target, and over what horizon?
- What's the risk tolerance — max acceptable drawdown?
- Fully automated or decision-support for manual execution?
- Cash equity only, or willing to use F&O?

These answers determine which additions are viable and in what order.
