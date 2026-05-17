"""Tests for replay_strategy() — pluggable Strategy ABC in the replay harness.

Covers:
- replay_strategy accepts any Strategy subclass
- GapStrategy.signal() returns a Trade on qualifying data, None otherwise
- replay_strategy produces same trade dates as GapStrategy.trades() on same data
- replay_strategy writes to DB with strategy name in signal column
- Unknown strategy raises NotImplementedError from signal()
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from core.backtester import GapStrategy, Strategy
from core.replay import replay_strategy, _trading_days
from core.knowledge_base import kb_path


# ── Synthetic data fixture ────────────────────────────────────────────────────

@pytest.fixture()
def synthetic_symbol(tmp_path, monkeypatch):
    symbol = "REPLAYTST"
    n = 80
    rng = np.random.default_rng(7)
    closes = 100 + np.cumsum(rng.normal(0.2, 0.5, n))
    opens  = closes.copy()
    highs  = opens * 1.02
    lows   = opens * 0.98
    vols   = np.ones(n) * 1000

    for idx in (50, 62, 71):
        opens[idx]  = closes[idx - 1] * 1.03
        highs[idx]  = opens[idx] * 1.04
        lows[idx]   = opens[idx] * 0.99
        vols[idx]   = 3000

    dates = pd.bdate_range("2024-01-01", periods=n)
    df = pd.DataFrame({
        "Open": opens, "High": highs, "Low": lows,
        "Close": closes, "Volume": vols,
    }, index=dates)

    sym_dir = tmp_path / symbol
    sym_dir.mkdir()
    df.to_parquet(sym_dir / "price_history.parquet")

    monkeypatch.setattr("core.replay.kb_path",     lambda s: sym_dir if s == symbol else kb_path(s))
    monkeypatch.setattr("core.backtester.kb_path", lambda s: sym_dir if s == symbol else kb_path(s))

    return symbol, df


# ── GapStrategy.signal() ──────────────────────────────────────────────────────

def test_gap_signal_returns_trade_on_qualifying_row(synthetic_symbol):
    symbol, df = synthetic_symbol
    strat = GapStrategy()
    pit = df.iloc[:51]  # index 50 is the gap day
    trade = strat.signal(pit, symbol)
    assert trade is not None
    assert trade.symbol == symbol
    assert trade.strategy == "gap"
    assert trade.entry > 0


def test_gap_signal_returns_none_on_non_gap_row(synthetic_symbol):
    symbol, df = synthetic_symbol
    pit = df.iloc[:50]  # day before the gap — no gap on last row
    assert GapStrategy().signal(pit, symbol) is None


def test_gap_signal_returns_none_on_short_df(synthetic_symbol):
    symbol, df = synthetic_symbol
    assert GapStrategy().signal(df.iloc[:5], symbol) is None


# ── Base Strategy raises NotImplementedError ──────────────────────────────────

def test_base_strategy_signal_raises():
    class Bare(Strategy):
        name = "bare"
        def trades(self, symbol):
            return iter([])

    with pytest.raises(NotImplementedError):
        Bare().signal(pd.DataFrame(), "X")


# ── replay_strategy() ─────────────────────────────────────────────────────────

def test_replay_strategy_returns_dataframe(synthetic_symbol, tmp_path):
    symbol, df = synthetic_symbol
    start = df.index[0].date()
    end   = df.index[-1].date()
    db    = tmp_path / "r.db"

    result = replay_strategy(GapStrategy(), [symbol], start, end, db_path=db)
    assert isinstance(result, pd.DataFrame)
    assert db.exists()


def test_replay_strategy_signal_column_matches_strategy_name(synthetic_symbol, tmp_path):
    symbol, df = synthetic_symbol
    result = replay_strategy(
        GapStrategy(), [symbol],
        df.index[0].date(), df.index[-1].date(),
        db_path=tmp_path / "r.db",
    )
    if not result.empty:
        assert (result["signal"] == "gap").all()


def test_replay_strategy_matches_gap_strategy_trades(synthetic_symbol, tmp_path):
    """replay_strategy(GapStrategy) must produce same trade dates as GapStrategy.trades()."""
    symbol, df = synthetic_symbol
    start = df.index[0].date()
    end   = df.index[-1].date()

    result = replay_strategy(
        GapStrategy(), [symbol], start, end,
        db_path=tmp_path / "r.db",
    )
    replay_dates = set(result["date"].tolist()) if not result.empty else set()

    gs_dates = {str(t.entry_dt.date()) for t in GapStrategy().trades(symbol)}

    assert replay_dates == gs_dates


def test_replay_strategy_empty_for_no_data(tmp_path, monkeypatch):
    """Symbol with no parquet → empty result, no crash."""
    monkeypatch.setattr("core.replay.kb_path", lambda s: tmp_path / s)
    result = replay_strategy(
        GapStrategy(), ["NOSYM"],
        date(2024, 1, 1), date(2024, 1, 31),
        db_path=tmp_path / "r.db",
    )
    assert result.empty


def test_replay_strategy_custom_strategy(synthetic_symbol, tmp_path):
    """A custom Strategy subclass plugs in correctly."""
    symbol, df = synthetic_symbol

    class AlwaysTrade(Strategy):
        name = "always"
        def trades(self, symbol):
            return iter([])
        def signal(self, pit_df, symbol):
            if len(pit_df) < 2:
                return None
            row = pit_df.iloc[-1]
            from core.backtester import Trade
            import pandas as pd
            return Trade(
                symbol=symbol,
                entry_dt=pit_df.index[-1], exit_dt=pit_df.index[-1],
                entry=float(row["Open"]), exit=float(row["Close"]),
                qty=1, exit_reason="Close", strategy=self.name,
            )

    result = replay_strategy(
        AlwaysTrade(), [symbol],
        df.index[0].date(), df.index[-1].date(),
        db_path=tmp_path / "r.db",
    )
    assert not result.empty
    assert (result["signal"] == "always").all()
