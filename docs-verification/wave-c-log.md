# Wave C — Larger items

> Started: 2026-05-16 21:00 IST.

## Plan

| Step | Item                                                       | Effort | Status   |
|------|------------------------------------------------------------|--------|----------|
| C.1  | Cache market context for ml_model.predict                  | 0.5h   | ✅ done  |
| C.2  | Replace yfinance LTP with Groww in execution_agent         | 1h     | ✅ done  |
| C.3  | Wire ExecutionAgent through Broker abstraction              | 1h     | ✅ done  |
| C.4  | Backtester consolidation (P1 §12)                          | 2h     | ⏸ deferred — see note below |
| C.5  | Final docs sweep                                            | 30m    | ⏳ next |

## Notes

### Why C.4 deferred
Three+ backtest scripts (`core/backtester.py`, `backtest_gap.py`,
`backtest_intraday.py`, plus dashboard Tab 3) each have:
* their own CLI flags / hard-coded thresholds,
* their own filtering & exit logic (gap-fill SL, trailing-stop walks, EOD),
* outputs in slightly different table shapes.

A clean consolidation would expose all four as `Strategy` subclasses
under `core/backtester.py` with a single CLI like
`python -m core.backtester --strategy gap --threshold 2.0` etc. That's
~2 days of careful refactoring + re-running every backtest the user
already has report numbers from. Doing it inline without confirmation
risks producing different numbers than the dashboard the user knows.

Marking deferred and adding a roadmap entry in
`docs/analysis/06-improvements.md`. The pieces it depends on (B.4 config,
core/costs, retry, concurrency) are all in place.

**Wave C: 3 of 4 items shipped (C.1, C.2, C.3); C.4 outlined-only.**
**78 unit tests, all green. Wall time: ~1 h.**
