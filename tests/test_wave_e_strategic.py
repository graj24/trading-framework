"""Tests for C.4 backtester, P2§18 stock regime, P2§19 P&L attribution, P2§25 replay."""
from __future__ import annotations

import sqlite3
import uuid
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_ohlcv(n=100, gap_day: int | None = None, seed=42) -> pd.DataFrame:
    """Synthetic daily OHLCV. If gap_day is set, that row has a 3.5% gap-up
    and passes all GapStrategy filters (volume, EMA50, MACD)."""
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2024-01-02", periods=n, freq="B")
    # Gently trending up so EMA50 is below price and MACD is bullish
    close = 100 + np.arange(n) * 0.3 + np.cumsum(rng.normal(0, 0.5, n))
    close = np.maximum(close, 10)
    vol_base = 1_000_000.0
    df = pd.DataFrame({
        "Open":   close * (1 + rng.uniform(-0.003, 0.003, n)),
        "High":   close * (1 + rng.uniform(0.005, 0.015, n)),
        "Low":    close * (1 - rng.uniform(0.005, 0.015, n)),
        "Close":  close,
        "Volume": np.full(n, vol_base),
    }, index=dates)
    if gap_day is not None and gap_day < n:
        prev_close = df["Close"].iloc[gap_day - 1]
        df.loc[df.index[gap_day], "Open"]   = prev_close * 1.04   # 4% gap
        df.loc[df.index[gap_day], "High"]   = prev_close * 1.07
        df.loc[df.index[gap_day], "Low"]    = prev_close * 1.025
        df.loc[df.index[gap_day], "Close"]  = prev_close * 1.06
        df.loc[df.index[gap_day], "Volume"] = vol_base * 3.0      # 3× avg → passes filter
    return df


# ── C.4 GapStrategy ───────────────────────────────────────────────────────────

class TestGapStrategy:
    def test_yields_trade_on_qualifying_gap(self, tmp_path, monkeypatch):
        from core.backtester import GapStrategy
        import core.knowledge_base as kb

        sym = "TESTSYM"
        sym_dir = tmp_path / sym
        sym_dir.mkdir()
        df = _make_ohlcv(n=100, gap_day=80)
        df.index = df.index.tz_localize("UTC")
        df.to_parquet(sym_dir / "price_history.parquet")
        monkeypatch.setattr(kb, "STOCKS_DIR", tmp_path)

        trades = list(GapStrategy(threshold=2.0).trades(sym))
        assert len(trades) >= 1
        t = trades[0]
        assert t.symbol == sym
        assert t.pnl_pct != 0.0
        assert t.qty >= 1

    def test_no_trade_below_threshold(self, tmp_path, monkeypatch):
        from core.backtester import GapStrategy
        import core.knowledge_base as kb

        sym = "TESTSYM"
        sym_dir = tmp_path / sym
        sym_dir.mkdir()
        # No gap day — all opens within 1% of prev close
        df = _make_ohlcv(n=100)
        df.index = df.index.tz_localize("UTC")
        df.to_parquet(sym_dir / "price_history.parquet")
        monkeypatch.setattr(kb, "STOCKS_DIR", tmp_path)

        trades = list(GapStrategy(threshold=2.0).trades(sym))
        assert trades == []

    def test_missing_symbol_yields_nothing(self, tmp_path, monkeypatch):
        from core.backtester import GapStrategy
        import core.knowledge_base as kb
        monkeypatch.setattr(kb, "STOCKS_DIR", tmp_path)
        assert list(GapStrategy().trades("NOSYM")) == []

    def test_run_returns_dataframe(self, tmp_path, monkeypatch):
        from core.backtester import GapStrategy, run
        import core.knowledge_base as kb

        sym = "TESTSYM"
        (tmp_path / sym).mkdir()
        df = _make_ohlcv(n=100, gap_day=80)
        df.index = df.index.tz_localize("UTC")
        df.to_parquet(tmp_path / sym / "price_history.parquet")
        monkeypatch.setattr(kb, "STOCKS_DIR", tmp_path)

        result = run(GapStrategy(threshold=2.0), [sym])
        assert isinstance(result, pd.DataFrame)
        if not result.empty:
            assert "pnl_inr" in result.columns
            assert "win" in result.columns


# ── P2 §18 stock-specific regime ─────────────────────────────────────────────

class TestStockRegime:
    def _write_parquet(self, tmp_path, sym, df):
        import core.knowledge_base as kb
        sym_dir = tmp_path / sym
        sym_dir.mkdir(exist_ok=True)
        df.index = df.index.tz_localize("UTC")
        df.to_parquet(sym_dir / "price_history.parquet")

    def test_compute_stock_regime_returns_dict(self, tmp_path, monkeypatch):
        from agents.regime_agent import compute_stock_regime
        import core.knowledge_base as kb
        monkeypatch.setattr(kb, "STOCKS_DIR", tmp_path)

        df = _make_ohlcv(n=80)
        self._write_parquet(tmp_path, "SYM", df)
        result = compute_stock_regime("SYM")
        assert result is not None
        assert "regime" in result
        assert result["regime"] in {"trending_bull", "trending_bear", "ranging", "high_volatility"}
        assert "adx" in result
        assert result["source"] == "stock"

    def test_compute_stock_regime_missing_data_returns_none(self, tmp_path, monkeypatch):
        from agents.regime_agent import compute_stock_regime
        import core.knowledge_base as kb
        monkeypatch.setattr(kb, "STOCKS_DIR", tmp_path)
        assert compute_stock_regime("NOSYM") is None

    def test_blend_regimes_aligned(self):
        from agents.regime_agent import blend_regimes
        nifty  = {"regime": "trending_bull", "confidence": 0.8, "adx": 30.0}
        stock  = {"regime": "trending_bull", "confidence": 0.7}
        result = blend_regimes(nifty, stock)
        assert result["regime"] == "trending_bull"
        assert result["blend_note"] == "aligned"

    def test_blend_regimes_divergent(self):
        from agents.regime_agent import blend_regimes
        nifty  = {"regime": "trending_bull", "confidence": 0.8, "adx": 30.0}
        stock  = {"regime": "trending_bear", "confidence": 0.7}
        result = blend_regimes(nifty, stock)
        assert "divergent" in result["blend_note"]
        assert result["stock_regime"] == "trending_bear"

    def test_blend_regimes_no_stock_data(self):
        from agents.regime_agent import blend_regimes
        nifty  = {"regime": "ranging", "confidence": 0.6}
        result = blend_regimes(nifty, None)
        assert result["regime"] == "ranging"
        assert result["blend_note"] == "nifty_only"
        assert result["stock_regime"] is None


# ── P2 §19 P&L attribution ────────────────────────────────────────────────────

class TestSignalAttribution:
    def _make_db(self, tmp_path) -> Path:
        db = tmp_path / "test_trades.db"
        conn = sqlite3.connect(db)
        conn.execute("""
            CREATE TABLE trades (
                id TEXT PRIMARY KEY, symbol TEXT, entry_date TEXT,
                entry_price REAL, stop_loss REAL, target REAL,
                position_size REAL, exit_date TEXT, exit_price REAL,
                pnl_pct REAL, pnl_inr REAL, outcome TEXT DEFAULT 'open',
                reasoning TEXT, created_at TEXT, technical_score REAL,
                sentiment REAL, pattern_ev REAL, sector_momentum REAL,
                regime_alignment REAL, weights_applied INTEGER DEFAULT 0,
                signal_source TEXT
            )
        """)
        rows = [
            ("t1", "RELIANCE", "2026-01-01", 100, 95, 110, 1500, "2026-01-02", 110,
             10.0, 150.0, "win", "", "2026-01-01", None, None, None, None, None, 1, "gap"),
            ("t2", "TCS", "2026-01-02", 200, 190, 220, 2000, "2026-01-03", 195,
             -2.5, -50.0, "loss", "", "2026-01-02", None, None, None, None, None, 1, "gap"),
            ("t3", "INFY", "2026-01-03", 150, 140, 165, 1500, "2026-01-04", 160,
             6.7, 100.0, "win", "", "2026-01-03", None, None, None, None, None, 1, "ml_daily"),
            ("t4", "HDFCBANK", "2026-01-04", 300, 285, 330, 3000, "open", None,
             None, None, "open", "", "2026-01-04", None, None, None, None, None, 0, "gap"),
        ]
        conn.executemany(
            "INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows
        )
        conn.commit()
        conn.close()
        return db

    def test_attribution_groups_by_source(self, tmp_path, monkeypatch):
        from agents.execution_agent import ExecutionAgent
        import agents.execution_agent as ea
        db = self._make_db(tmp_path)
        monkeypatch.setattr(ea, "DB_PATH", db)

        agent = ExecutionAgent({"trading": {"mode": "paper", "capital": 10000}})
        result = agent.signal_attribution()
        sources = {r["signal_source"] for r in result}
        assert "gap" in sources
        assert "ml_daily" in sources

    def test_attribution_excludes_open_trades(self, tmp_path, monkeypatch):
        from agents.execution_agent import ExecutionAgent
        import agents.execution_agent as ea
        db = self._make_db(tmp_path)
        monkeypatch.setattr(ea, "DB_PATH", db)

        agent = ExecutionAgent({"trading": {"mode": "paper", "capital": 10000}})
        result = agent.signal_attribution()
        gap_row = next(r for r in result if r["signal_source"] == "gap")
        # t4 is open — should not be counted
        assert gap_row["trades"] == 2

    def test_attribution_win_rate(self, tmp_path, monkeypatch):
        from agents.execution_agent import ExecutionAgent
        import agents.execution_agent as ea
        db = self._make_db(tmp_path)
        monkeypatch.setattr(ea, "DB_PATH", db)

        agent = ExecutionAgent({"trading": {"mode": "paper", "capital": 10000}})
        result = agent.signal_attribution()
        gap_row = next(r for r in result if r["signal_source"] == "gap")
        # 1 win, 1 loss → 50%
        assert gap_row["win_rate"] == pytest.approx(50.0)

    def test_attribution_sorted_by_pnl(self, tmp_path, monkeypatch):
        from agents.execution_agent import ExecutionAgent
        import agents.execution_agent as ea
        db = self._make_db(tmp_path)
        monkeypatch.setattr(ea, "DB_PATH", db)

        agent = ExecutionAgent({"trading": {"mode": "paper", "capital": 10000}})
        result = agent.signal_attribution()
        pnls = [r["total_pnl_inr"] for r in result]
        assert pnls == sorted(pnls, reverse=True)

    def test_signal_source_persisted_in_execute_trade(self, tmp_path, monkeypatch):
        from agents.execution_agent import ExecutionAgent, migrate_trades_schema
        import agents.execution_agent as ea
        db = tmp_path / "trades.db"
        monkeypatch.setattr(ea, "DB_PATH", db)
        migrate_trades_schema(db)

        agent = ExecutionAgent({"trading": {"mode": "paper", "capital": 10000}})
        agent.execute_trade(
            symbol="RELIANCE", entry_price=100, stop_loss=95, target=110,
            position_size=1500, reasoning="test",
            signals_at_entry={"signal_source": "gap", "technical_score": 7.5},
        )
        conn = sqlite3.connect(db)
        row = conn.execute("SELECT signal_source FROM trades LIMIT 1").fetchone()
        conn.close()
        assert row[0] == "gap"


# ── P2 §25 replay harness ─────────────────────────────────────────────────────

class TestReplayHarness:
    def _write_parquet(self, tmp_path, sym, df):
        import core.knowledge_base as kb
        sym_dir = tmp_path / sym
        sym_dir.mkdir(exist_ok=True)
        df.index = df.index.tz_localize("UTC")
        df.to_parquet(sym_dir / "price_history.parquet")

    def test_replay_returns_dataframe(self, tmp_path, monkeypatch):
        from core.replay import replay
        import core.knowledge_base as kb
        monkeypatch.setattr(kb, "STOCKS_DIR", tmp_path)

        df = _make_ohlcv(n=100, gap_day=80)
        self._write_parquet(tmp_path, "SYM", df)

        db = tmp_path / "replay.db"
        result = replay(
            ["SYM"],
            start=date(2024, 1, 2),
            end=date(2024, 6, 30),
            db_path=db,
        )
        assert isinstance(result, pd.DataFrame)

    def test_replay_writes_to_separate_db(self, tmp_path, monkeypatch):
        from core.replay import replay
        import core.knowledge_base as kb
        monkeypatch.setattr(kb, "STOCKS_DIR", tmp_path)

        df = _make_ohlcv(n=100, gap_day=80)
        self._write_parquet(tmp_path, "SYM", df)

        db = tmp_path / "replay_test.db"
        replay(["SYM"], date(2024, 1, 2), date(2024, 6, 30), db_path=db)
        assert db.exists()
        conn = sqlite3.connect(db)
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        conn.close()
        assert "replay_trades" in tables

    def test_replay_point_in_time_slicing(self, tmp_path, monkeypatch):
        """Trades on day D should only use data available up to D."""
        from core.replay import _pit_slice
        import core.knowledge_base as kb
        monkeypatch.setattr(kb, "STOCKS_DIR", tmp_path)

        df = _make_ohlcv(n=100)
        self._write_parquet(tmp_path, "SYM", df)

        cutoff = df.index[49].date()
        sliced = _pit_slice("SYM", cutoff)
        assert sliced is not None
        assert sliced.index[-1].date() <= cutoff
        assert len(sliced) <= 50

    def test_replay_missing_symbol_produces_no_trades(self, tmp_path, monkeypatch):
        from core.replay import replay
        import core.knowledge_base as kb
        monkeypatch.setattr(kb, "STOCKS_DIR", tmp_path)

        db = tmp_path / "replay.db"
        result = replay(["NOSYM"], date(2024, 1, 2), date(2024, 3, 31), db_path=db)
        assert result.empty
