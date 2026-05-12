# Improvements Backlog

Living document. Add new items as they come up. Priority ordering is rough — revisit when planning phases.

**Related docs:**
- Overall roadmap → `docs/specs/2026-05-12-phased-roadmap-design.md`
- Current phase tasks → `docs/tasks/2026-05-12-phase-0-critical-fixes.md`

## Decision-Logic Improvements

1. **Learned composite scoring weights**
   The rule-based fallback in `agents/master.py` uses hardcoded weights (0.40/0.30/0.30 for tech/sentiment/pattern). These should be dynamically learned from `LearningAgent` based on trade outcomes per stock and per regime. Infrastructure exists — `signal_weights.json` in KB — just needs wiring into composite calculation. **Targeted for Phase 1 (per-regime learning weights task).**

2. **ATR / structure-based stop-loss and targets**
   Currently SL = price × 0.99, target = price × 1.025. Should use:
   - ATR-based levels (1.5× ATR below entry for SL)
   - Structure-based levels (recent swing low for SL, resistance zone for target)
   - Feed support/resistance zones from `technical_agent` into the LLM prompt so it can pick meaningful levels.
   **Targeted for Phase 0 (task T0.2).**

## Data / Feature Improvements

3. **Real news depth** — MoneyControl, Economic Times scrapers are empty stubs in `news_agent.py`. Targeted for Phase 0 (task T0.6 covers NSE; consider extending).

4. **Alternative data sources** — StockTwits, Google Trends (via pytrends), Finnhub free tier. Not critical, evaluate in Phase 1 if baseline is borderline.

5. **Better pattern recognition** — `PatternAgent` uses DTW on raw prices with only 5 matches. Statistical significance is weak. Consider:
   - Include volume and volatility in the feature vector.
   - Require minimum 20 matches before trusting EV.
   - Or deprecate in favor of learned features.

## Architecture Improvements

6. **Centralized feature store** — Each agent currently refetches its own data. A shared feature-computation layer would eliminate duplication and make strategies pluggable. **Happens implicitly during Phase 0 data work; explicitly before Phase 3 strategy router.**

7. **Database migration** — SQLite → Postgres when trade volume exceeds ~10K rows or when multiple strategies run concurrently. Low priority.

8. **Backtesting engine upgrade** — `core/backtester.py` works for simple cases. Options / multi-strategy testing may need backtrader or vectorbt. Evaluate in Phase 3.

## Risk / Execution Improvements

9. **Paper-to-live divergence measurement** — Built into Phase 2 as explicit deliverable.

10. **Kill-switch via Telegram command** — Stretch goal in Phase 0 (task T0.8), formal in Phase 2.

11. **Per-regime position sizing** — Current Kelly is regime-agnostic. Should de-size in high_volatility, up-size in trending_bull. Partially exists via `STRATEGY_ADJUSTMENTS` in regime agent but not wired through. Evaluate in Phase 3.

## Research / Learnings

12. **Walk-forward validation discipline** — No current out-of-sample testing. Add to Phase 1 (explicit deliverable).

13. **Monte Carlo robustness** — Resample trade sequence to estimate distribution of outcomes (not just point estimate). Add once baseline is established in Phase 1.

## Tech Debt

14. ~~Hardcoded paths in `ripple/config.py` and `ripple/pipeline.py`~~ — **fixed 2026-05-12**.
15. ~~`twitter_collector.py` misnamed~~ — **renamed to `data_collector.py` 2026-05-12**.
16. `agents/execution_agent.py` transaction costs oversimplified — **Phase 0 task T0.3**.

## Questions to Resolve

- Does Groww API support WebSocket quotes or REST only? Decides intraday feasibility.
- Does Groww API expose option chain? If not, NSE direct or Kite Connect.
- Groww brokerage structure — flat ₹20 or percentage-based? Affects small-capital economics.
