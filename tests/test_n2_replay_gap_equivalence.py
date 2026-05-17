"""N-2: replay._gap_signal must produce the same trade dates as GapStrategy.

Builds a synthetic price_history.parquet, runs both paths, asserts equality.
This test would have caught B-1 before the fix.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from core.replay import _gap_signal, _pit_slice, _trading_days
from core.backtester import GapStrategy
from core.knowledge_base import kb_path


@pytest.fixture()
def synthetic_symbol(tmp_path, monkeypatch):
    """Write a synthetic price_history.parquet and patch kb_path to point there."""
    symbol = "TESTSYM"

    # 80 trading days; engineer 3 gap-up days that pass all 4 filters
    n = 80
    rng = np.random.default_rng(42)
    closes = 100 + np.cumsum(rng.normal(0.2, 0.5, n))  # gentle uptrend
    opens  = closes.copy()
    highs  = opens * 1.02
    lows   = opens * 0.98
    vols   = np.ones(n) * 1000

    # Inject 3 gap-up days at indices 50, 60, 70
    for idx in (50, 60, 70):
        opens[idx]  = closes[idx - 1] * 1.03   # 3% gap
        highs[idx]  = opens[idx] * 1.04
        lows[idx]   = opens[idx] * 0.99
        vols[idx]   = 3000                      # 3× avg → passes volume filter

    dates = pd.bdate_range("2024-01-01", periods=n)
    df = pd.DataFrame({
        "Open": opens, "High": highs, "Low": lows,
        "Close": closes, "Volume": vols,
    }, index=dates)

    sym_dir = tmp_path / symbol
    sym_dir.mkdir()
    df.to_parquet(sym_dir / "price_history.parquet")

    monkeypatch.setattr("core.replay.kb_path",      lambda s: sym_dir if s == symbol else kb_path(s))
    monkeypatch.setattr("core.backtester.kb_path",  lambda s: sym_dir if s == symbol else kb_path(s))

    return symbol, df


def test_replay_matches_gap_strategy(synthetic_symbol):
    symbol, df = synthetic_symbol

    start = df.index[0].date()
    end   = df.index[-1].date()

    # --- replay path ---
    replay_dates = set()
    for d in _trading_days(start, end):
        pit = df[df.index.date <= d]
        if len(pit) < 2 or pit.index[-1].date() != d:
            continue
        trade = _gap_signal(pit)
        if trade:
            replay_dates.add(trade["date"])

    # --- GapStrategy path ---
    gs_dates = {str(t.entry_dt.date()) for t in GapStrategy().trades(symbol)}

    assert replay_dates == gs_dates, (
        f"Mismatch:\n  replay only: {replay_dates - gs_dates}\n"
        f"  GapStrategy only: {gs_dates - replay_dates}"
    )
