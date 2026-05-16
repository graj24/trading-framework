"""Centralised trading-cost constants.

Until 2026-05-16, slippage and brokerage values diverged across modules:

| Module                          | SLIPPAGE | BROKERAGE |
|---------------------------------|----------|-----------|
| agents/execution_agent.py       | 0.0005   | 0.0003    |
| core/backtester.py              | 0.0005   | 0.0003    |
| backtest_intraday.py            | 0.001    | 0.0003    |
| backtest_gap.py                 | 0.001    | (inline)  |
| simulate_day.py                 | 0.001    | 0.0003    |
| dashboard.py:pnl()              | 0.06% (lump-sum) | n/a |

This module canonicalises them so backtest reports are directly
comparable to live paper P&L. See docs-verification/findings.md MED-7
and docs/analysis/05-issues.md §B4.

Constants are *fractions*, not bps and not percent — i.e.
``SLIPPAGE_FRAC = 0.0005`` means 5 bps (0.05%) one-sided.
"""
from __future__ import annotations

# One-sided slippage applied to every fill (buy at +SLIPPAGE_FRAC,
# sell at -SLIPPAGE_FRAC). 5 bps is the historical default in the
# event-driven backtester — it slightly under-states real Indian
# large-cap intraday slippage but stays consistent.
SLIPPAGE_FRAC: float = 0.0005

# Brokerage charged on each side of a trade (3 bps).
BROKERAGE_FRAC: float = 0.0003

# Securities Transaction Tax — applied only on the SELL side, 10 bps.
STT_SELL_FRAC: float = 0.001

# Convenience: total round-trip cost as a fraction of notional.
# Two slippages (entry + exit) + two brokerages + one STT on sell.
ROUND_TRIP_COST_FRAC: float = 2 * SLIPPAGE_FRAC + 2 * BROKERAGE_FRAC + STT_SELL_FRAC


def cost_per_side(notional: float) -> float:
    """INR cost of a single side of a trade (brokerage + slippage)."""
    return notional * (BROKERAGE_FRAC + SLIPPAGE_FRAC)


def cost_round_trip(notional: float) -> float:
    """INR cost of an entry + exit pair, including STT."""
    return notional * ROUND_TRIP_COST_FRAC
