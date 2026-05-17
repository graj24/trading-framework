# Backtest Results — Post B-1 Fix

> Generated: 2026-05-17  
> Branch: `feat/bloomberg-ui`  
> Strategy: `GapStrategy` (threshold=2.0%, capital=₹10,000, position=15%)  
> Data: 10 NSE symbols, 2021-05-14 → 2026-05-11 (~5 years)  
> **These are the canonical numbers after the B-1 MACD-filter fix.**  
> Pre-fix replay reports were biased upward (MACD filter was not applied).

## Headline

| Metric          | Value                  |
|-----------------|------------------------|
| Symbols         | 10                     |
| Total trades    | 59                     |
| Win rate        | 54.2% (32W / 27L)      |
| Total net P&L   | ₹+818.27               |
| Profit factor   | 1.87×                  |
| Avg P&L / trade | +₹13.87 / +1.047%      |
| Best trade      | +8.84%                 |
| Worst trade     | −4.05%                 |
| Date range      | 2021-05-23 → 2026-05-10 |

## Per-symbol breakdown

| Symbol     | Trades | Win%  | Net P&L (₹) |
|------------|-------:|------:|------------:|
| ETERNAL    |     17 | 64.7% |    +439.40  |
| HDFCBANK   |      7 | 28.6% |      +5.64  |
| INDIGO     |      5 | 40.0% |     −38.64  |
| INFY       |      4 | 25.0% |     −70.57  |
| RELIANCE   |      2 |100.0% |     +50.18  |
| SBIN       |      5 | 60.0% |     +28.77  |
| TATACONSUM |      3 | 66.7% |     +73.57  |
| TCS        |      1 |  0.0% |    −107.45  |
| TITAN      |      4 |100.0% |    +337.29  |
| VEDL       |     11 | 45.5% |    +100.07  |

## Notes

- Capital per run is ₹10,000 with 15% position sizing — these are small-lot numbers.
  Scale linearly for larger capital.
- `GapStrategy` is the authoritative backtest path. `core/replay.py` runs the same
  logic day-by-day with point-in-time data slicing; trade counts differ slightly
  because replay skips days where the parquet's last row doesn't align exactly with
  the trading day (data gaps). The N-2 equivalence test verifies they agree on
  identical data.
- TCS is the worst performer (1 trade, −₹107.45). Low trade count — not statistically
  meaningful.
- TITAN and ETERNAL drive most of the positive P&L.
