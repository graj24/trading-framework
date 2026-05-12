# Phased Roadmap: A → F — Design Doc

**Date**: 2026-05-12
**Goal**: Maximize absolute returns from an automated trading framework for Indian equities, starting from the current intraday-swing strategy (A) and evolving to a multi-strategy regime-routed system (F).

**Brainstorming context**: See `docs/analysis/2026-05-12-framework-analysis.md` for the framework analysis that drove this roadmap.

## Constraints

- **Capital**: Start ₹10K for paper/live validation, scale to ₹10L+ only after baseline profitability proven.
- **Broker**: Groww API (real orders + quotes). Zerodha Kite Connect considered as secondary for data if Groww rate-limits are restrictive.
- **Execution**: Fully automated.
- **Max drawdown**: 20% — system pauses for review at this threshold.
- **Market**: NSE equities (initially), then NSE F&O.
- **Approach chosen**: Sequential — each phase proven before next starts, with feature-layer refactoring happening inside Phase 0 so data access stays modular throughout.

## Strategy Evolution

| Phase | Strategy Style | Regime Coverage |
|-------|---------------|-----------------|
| Phase 1-2 | A: Intraday swing (long-only equity cash) | `trending_bull` only (effectively) |
| Phase 3 | + Mean-reversion for `ranging` | `trending_bull`, `ranging` |
| Phase 4 | + Shorts via futures for `trending_bear`, + Options for `high_volatility` | All four regimes (= F) |

## Phase 0 — Critical Fixes (~2-3 weeks)

**Objective**: Make A production-ready on real data. No strategy changes, only infrastructure + data quality.

### Deliverables

1. **Groww API as primary data source**
   - Replace yfinance in `data_agent.py` price fetch path.
   - Real-time quotes via Groww WebSocket (if supported) or REST polling.
   - yfinance retained as fallback only.
2. **Wire ATR-based stop-loss and target**
   - `agents/master.py` currently uses `price * 0.99` and `price * 1.025`. Use `risk_manager.atr_stop_loss()` (already implemented) and compute target as `entry + N × ATR` with N tunable.
3. **Realistic transaction cost model**
   - STT (0.025% sell side for intraday, 0.1% buy+sell for delivery), NSE exchange charges (0.00325%), stamp duty (0.003%), GST (18% on brokerage + exchange charges), SEBI charges.
   - Update `agents/execution_agent.py` `_pnl()` to reflect.
4. **FII/DII flow ingestion**
   - Scrape NSDL daily FII/DII flow data.
   - Store in knowledge base as a market-wide feature (not per-stock).
   - Expose to `MasterAgent` scoring.
5. **NSE option chain ingestion**
   - Pull Nifty + BankNifty + watchlist-stock option chains.
   - Compute PCR, max pain. Store daily snapshots.
   - Expose as market regime feature.
6. **Real NSE corporate announcements**
   - Replace empty stub in `news_agent.py`.
   - Pull from NSE's official announcements feed.
7. **Cloud VPS setup**
   - DigitalOcean or AWS t3.small running scheduler 24/7.
   - Dead-man's-switch via Healthchecks.io.
8. **Telegram alerts enabled**
   - Flip `telegram.enabled` to true, configure bot, test alerts.

### Success Criteria
- All 8 deliverables completed and verified.
- Scheduler runs for 5 consecutive days on VPS without manual intervention.
- Paper trades show realistic P&L after full transaction cost modeling.
- FII/DII + option chain + corporate announcements visible in daily logs.

## Phase 1 — Baseline Measurement (4-8 weeks)

**Objective**: Establish a profitable baseline for A via paper trading. No strategy changes during measurement.

### Deliverables

1. **Walk-forward validation** of all rule thresholds in `agents/master.py` (composite score cutoff, filter gate parameters, etc.) using the gap backtester extended to general swing trades.
2. **Per-regime learning weights** — extend `learning_agent.py` to track `(regime × signal)` weights instead of global weights.
3. **Discovery pipeline improvement** — DiscoveryAgent already uses Google Trends + volume + bulk deals. Add FII/DII sector-level flow tilt and option OI buildup as candidates.
4. **Measurement dashboard** — daily cron job that writes summary metrics: Sharpe, win rate, avg win, avg loss, max drawdown, trade count, regime distribution. Output to `docs/learnings/<date>-metrics.md`.
5. **Frozen strategy during measurement** — document this rule explicitly. No parameter changes mid-measurement window.

### Success Criteria
- 4+ weeks of continuous paper trades.
- Sharpe > 1.0 on paper (relaxed bar for paper, accounting for data lag).
- Win rate > 45%, expectancy > +0.3% per trade after costs (consistent with 1.67 R:R and stated win rate).
- Walk-forward validation shows filters hold on out-of-sample data.

### Go/No-Go Decision
- **If baseline profitable**: proceed to Phase 2 (go live small).
- **If not profitable**: go back to signal/feature work. Document why in `docs/learnings/`.

## Phase 2 — Live Trading at Small Scale (4-6 weeks)

**Objective**: Validate paper-to-live divergence. Identify execution issues before scaling.

### Deliverables

1. **Enable live mode** in `config.yaml` with capital ₹50K-1L.
2. **Kill switch** — manual command to close all positions and pause scheduler immediately.
3. **Live vs paper comparison** — run both simultaneously. Log divergence metrics weekly.
4. **Execution quality measurement** — slippage vs expected, fill rate, order rejection rate.
5. **Weekly review ritual** — human review every Sunday. Document in `docs/learnings/`.

### Success Criteria
- 4+ weeks of live trades with ≤ 30% P&L divergence from paper.
- No order execution bugs (rejections handled, partial fills handled, margin checks correct).
- Drawdown stays under 20% throughout.

### Go/No-Go Decision
- **If live profitable and stable**: scale capital (₹1L → ₹3L → ₹10L over 2-3 months), proceed to Phase 3.
- **If not stable**: pause, fix, repeat Phase 2.

## Phase 3 — Multi-Strategy Foundation (6-12 weeks)

**Objective**: Build F's skeleton — add strategies for `ranging` and `trending_bear` regimes.

### Deliverables

1. **Strategy router** — new agent that picks the active strategy based on regime. Refactor `MasterAgent` to consume strategy output rather than being the strategy.
2. **Mean-reversion strategy** for `ranging` regimes — range-bound stocks, RSI extremes, Bollinger reversion.
3. **Short strategy via stock futures** for `trending_bear` regimes — requires F&O universe, margin awareness.
4. **Strategy-level P&L attribution** — each strategy's performance tracked separately.
5. **Cross-strategy risk limits** — portfolio-level position and correlation caps across active strategies.

### Success Criteria
- 3 strategies active (A, mean-reversion, shorts) routing correctly by regime.
- Each strategy profitable standalone (Sharpe > 0.8).
- Portfolio-level Sharpe > A alone.

## Phase 4 — Options Overlay (later)

**Objective**: Add options strategies for `high_volatility` regimes and as risk-defining overlay on directional trades.

### Deliverables (planned, not yet detailed)

1. **Directional options** — buy calls on strong BUY signals, buy puts on strong SELL signals.
2. **Premium collection** — sell ATM/OTM calls/puts in `ranging` regimes.
3. **Protective hedges** — buy cheap OTM puts to cap drawdown.
4. **Greeks-aware sizing** — delta/theta/vega aware position limits.

## Open Items

- **Is Zerodha Kite Connect (₹2K/month) needed for data, or is Groww sufficient?** Decide in Phase 0.
- **Paid data vendor (TrueData/GDFL) for tick data** — decide after Phase 2, only if an intraday strategy emerges as high-value addition in Phase 3+.
- **Backtesting engine upgrade** — current `core/backtester.py` works for simple cases. May need a proper event-driven engine (backtrader, vectorbt) for options/multi-strategy testing in Phase 3+.
- **Database migration** (SQLite → Postgres) — only when trade volume exceeds ~10K rows.

## Non-Goals (out of scope for this roadmap)

- High-frequency trading (sub-second latency).
- US equities / international markets.
- Manual/discretionary trading support (we went fully automated).
- Crypto.

## Revision History

- 2026-05-12: Initial draft based on brainstorming session with user.
