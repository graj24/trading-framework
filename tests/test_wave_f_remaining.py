"""Tests for B9 EPS consensus, P2§21 shadow mode, P2§22 DuckDB, P3 sector rotation, P3 multi-broker."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest


# ── B9: EPS consensus ─────────────────────────────────────────────────────────

class TestEpsConsensus:
    def _mock_ticker(self, eps_estimate, eps_actual):
        """Build a mock yf.Ticker with earnings_history."""
        df = pd.DataFrame([{
            "epsEstimate": eps_estimate,
            "epsActual":   eps_actual,
        }])
        t = MagicMock()
        t.earnings_history = df
        t.info = {}
        return t

    def test_beat_detected(self):
        from agents.earnings_calendar_agent import fetch_eps_consensus
        with patch("yfinance.Ticker", return_value=self._mock_ticker(10.0, 12.0)):
            r = fetch_eps_consensus("RELIANCE")
        assert r["verdict"] == "BEAT"
        assert r["beat_pct"] == pytest.approx(20.0)

    def test_miss_detected(self):
        from agents.earnings_calendar_agent import fetch_eps_consensus
        with patch("yfinance.Ticker", return_value=self._mock_ticker(10.0, 8.0)):
            r = fetch_eps_consensus("TCS")
        assert r["verdict"] == "MISS"
        assert r["beat_pct"] == pytest.approx(-20.0)

    def test_inline_detected(self):
        from agents.earnings_calendar_agent import fetch_eps_consensus
        with patch("yfinance.Ticker", return_value=self._mock_ticker(10.0, 10.2)):
            r = fetch_eps_consensus("INFY")
        assert r["verdict"] == "INLINE"

    def test_network_failure_returns_unknown(self):
        from agents.earnings_calendar_agent import fetch_eps_consensus
        with patch("yfinance.Ticker", side_effect=Exception("network")):
            r = fetch_eps_consensus("HDFCBANK")
        assert r["verdict"] == "UNKNOWN"
        assert r["eps_estimate"] is None

    def test_score_result_uses_eps_consensus(self):
        from agents.earnings_calendar_agent import score_result
        eps = {"verdict": "BEAT", "beat_pct": 15.0, "eps_estimate": 10.0, "eps_actual": 11.5}
        r = score_result("quarterly results announced", eps_consensus=eps)
        assert r["verdict"] == "BEAT"
        assert r["signal"] == "BUY"
        assert r["confidence"] > 0.65

    def test_score_result_without_eps_still_works(self):
        from agents.earnings_calendar_agent import score_result
        r = score_result("strong profit growth beats expectations")
        assert r["verdict"] == "BEAT"


# ── P2 §21: Shadow mode ───────────────────────────────────────────────────────

class TestShadowMode:
    def test_shadow_broker_always_returns_paper_id(self):
        from core.broker import ShadowBroker
        sb = ShadowBroker()
        # No Zerodha creds → live leg skipped
        with patch.object(sb.paper, "place_order", return_value="paper-123") as mock_paper, \
             patch.object(sb.paper, "get_order_status", return_value={"price": 100.0}):
            oid = sb.place_order("RELIANCE", qty=1)
        assert oid == "paper-123"

    def test_shadow_broker_logs_fill_comparison(self):
        from core.broker import ShadowBroker
        sb = ShadowBroker()
        with patch.object(sb.paper, "place_order", return_value="p1"), \
             patch.object(sb.paper, "get_order_status", return_value={"price": 100.0}):
            sb.place_order("TCS", qty=2)
        log = sb.fill_comparison()
        assert len(log) == 1
        assert log[0]["symbol"] == "TCS"
        assert log[0]["paper_id"] == "p1"

    def test_get_broker_shadow_mode(self):
        from core.broker import get_broker, ShadowBroker
        cfg = {"trading": {"mode": "shadow"}}
        b = get_broker(cfg)
        assert isinstance(b, ShadowBroker)

    def test_get_broker_paper_mode(self):
        from core.broker import get_broker, PaperBroker
        cfg = {"trading": {"mode": "paper"}}
        assert isinstance(get_broker(cfg), PaperBroker)

    def test_execution_agent_shadow_mode_does_not_raise(self, tmp_path, monkeypatch):
        """Shadow mode should not raise even when live leg is unavailable."""
        from agents.execution_agent import ExecutionAgent, migrate_trades_schema
        import agents.execution_agent as ea
        db = tmp_path / "trades.db"
        monkeypatch.setattr(ea, "DB_PATH", db)
        migrate_trades_schema(db)

        from core.broker import ShadowBroker, PaperBroker
        sb = ShadowBroker()
        # Patch paper broker's place_order to succeed
        with patch.object(sb.paper, "place_order", return_value="shadow-1"), \
             patch.object(sb.paper, "get_order_status", return_value={"price": 100.0}):
            agent = ExecutionAgent({"trading": {"mode": "shadow", "capital": 10000}})
            agent._broker = sb
            # Should not raise
            agent.execute_trade("RELIANCE", 100, 95, 110, 1500)


# ── P2 §22: DuckDB ────────────────────────────────────────────────────────────

class TestDuckDBStore:
    def _write_parquet(self, tmp_path, symbol, df):
        sym_dir = tmp_path / symbol
        sym_dir.mkdir(exist_ok=True)
        df.to_parquet(sym_dir / "price_history.parquet")

    def _make_df(self, n=50):
        dates = pd.date_range("2024-01-02", periods=n, freq="B", tz="UTC")
        rng = np.random.default_rng(0)
        close = 100 + np.cumsum(rng.normal(0, 1, n))
        return pd.DataFrame({
            "Open": close, "High": close * 1.01, "Low": close * 0.99,
            "Close": close, "Volume": 1_000_000.0,
        }, index=dates)

    def test_symbol_history_returns_dataframe(self, tmp_path, monkeypatch):
        import core.duckdb_store as ds
        monkeypatch.setattr(ds, "STOCKS_DIR", tmp_path)
        df = self._make_df()
        self._write_parquet(tmp_path, "RELIANCE", df)

        result = ds.symbol_history("RELIANCE")
        assert not result.empty
        assert "Close" in result.columns

    def test_symbol_history_date_filter(self, tmp_path, monkeypatch):
        import core.duckdb_store as ds
        monkeypatch.setattr(ds, "STOCKS_DIR", tmp_path)
        df = self._make_df(n=100)
        self._write_parquet(tmp_path, "TCS", df)

        result = ds.symbol_history("TCS", start="2024-03-01", end="2024-04-30")
        if not result.empty:
            assert result.index.min() >= pd.Timestamp("2024-03-01")
            assert result.index.max() <= pd.Timestamp("2024-04-30")

    def test_symbol_history_missing_returns_empty(self, tmp_path, monkeypatch):
        import core.duckdb_store as ds
        monkeypatch.setattr(ds, "STOCKS_DIR", tmp_path)
        result = ds.symbol_history("NOSYM")
        assert result.empty

    def test_multi_symbol_stacks_correctly(self, tmp_path, monkeypatch):
        import core.duckdb_store as ds
        monkeypatch.setattr(ds, "STOCKS_DIR", tmp_path)
        for sym in ["RELIANCE", "TCS"]:
            self._write_parquet(tmp_path, sym, self._make_df())

        result = ds.multi_symbol(["RELIANCE", "TCS"])
        assert not result.empty
        assert "symbol" in result.columns
        assert set(result["symbol"].unique()) == {"RELIANCE", "TCS"}

    def test_query_raw_sql(self, tmp_path, monkeypatch):
        import core.duckdb_store as ds
        monkeypatch.setattr(ds, "STOCKS_DIR", tmp_path)
        df = self._make_df()
        self._write_parquet(tmp_path, "INFY", df)
        path = tmp_path / "INFY" / "price_history.parquet"

        result = ds.query(f"SELECT COUNT(*) AS n FROM read_parquet('{path}')")
        assert not result.empty
        assert int(result["n"].iloc[0]) == 50


# ── P3: Sector rotation ───────────────────────────────────────────────────────

class TestSectorRotation:
    def _mock_returns(self):
        return {
            "it":    {"ret_1m": 5.0, "ret_3m": 12.0, "rank_1m": 1, "rank_3m": 1},
            "fmcg":  {"ret_1m": 2.0, "ret_3m":  4.0, "rank_1m": 2, "rank_3m": 2},
            "metal": {"ret_1m": -3.0, "ret_3m": -8.0, "rank_1m": 3, "rank_3m": 3},
        }

    def test_strong_signal_for_top_sector(self):
        from agents.sector_rotation_agent import sector_signal_for_stock, SECTOR_MAP
        returns = self._mock_returns()
        with patch("yfinance.Ticker") as mock_yf:
            mock_yf.return_value.info = {"sector": "Technology"}
            result = sector_signal_for_stock("TCS", returns)
        assert result["signal"] == "STRONG"
        assert result["sector_index"] == "it"

    def test_weak_signal_for_bottom_sector(self):
        from agents.sector_rotation_agent import sector_signal_for_stock
        returns = self._mock_returns()
        with patch("yfinance.Ticker") as mock_yf:
            mock_yf.return_value.info = {"sector": "Basic Materials"}
            result = sector_signal_for_stock("TATASTEEL", returns)
        assert result["signal"] == "WEAK"

    def test_neutral_when_sector_unknown(self):
        from agents.sector_rotation_agent import sector_signal_for_stock
        returns = self._mock_returns()
        with patch("yfinance.Ticker") as mock_yf:
            mock_yf.return_value.info = {"sector": "Utilities"}  # not in SECTOR_MAP
            result = sector_signal_for_stock("POWERGRID", returns)
        assert result["signal"] == "NEUTRAL"

    def test_agent_run_returns_result(self):
        from agents.sector_rotation_agent import SectorRotationAgent
        agent = SectorRotationAgent({})
        with patch.object(agent, "_get_sector_returns", return_value=self._mock_returns()):
            result = agent.run()
        assert result.ok()
        assert "sector_returns" in result.data
        assert "top_sectors" in result.data


# ── P3: Multi-broker stubs ────────────────────────────────────────────────────

class TestMultiBrokerStubs:
    def test_upstox_broker_instantiates(self):
        from core.broker import UpstoxBroker
        b = UpstoxBroker(api_key="test", access_token="test")
        assert b.api_key == "test"

    def test_upstox_place_order_raises_not_implemented(self):
        from core.broker import UpstoxBroker
        b = UpstoxBroker(api_key="k", access_token="t")
        with pytest.raises(NotImplementedError):
            b.place_order("RELIANCE", qty=1)

    def test_angelone_broker_instantiates(self):
        from core.broker import AngelOneBroker
        b = AngelOneBroker(api_key="k", client_id="c", password="p", totp_secret="t")
        assert b.client_id == "c"

    def test_angelone_place_order_raises_not_implemented(self):
        from core.broker import AngelOneBroker
        b = AngelOneBroker(api_key="k", client_id="c", password="p", totp_secret="t")
        with pytest.raises(NotImplementedError):
            b.place_order("TCS", qty=1)

    def test_get_broker_upstox(self):
        from core.broker import get_broker, UpstoxBroker
        cfg = {"trading": {"mode": "live", "broker": "upstox"}}
        with patch.dict("os.environ", {"UPSTOX_API_KEY": "k", "UPSTOX_ACCESS_TOKEN": "t"}):
            b = get_broker(cfg)
        assert isinstance(b, UpstoxBroker)

    def test_get_broker_angelone(self):
        from core.broker import get_broker, AngelOneBroker
        cfg = {"trading": {"mode": "live", "broker": "angelone"}}
        with patch.dict("os.environ", {
            "ANGELONE_API_KEY": "k", "ANGELONE_CLIENT_ID": "c",
            "ANGELONE_PASSWORD": "p", "ANGELONE_TOTP_SECRET": "t",
        }):
            b = get_broker(cfg)
        assert isinstance(b, AngelOneBroker)
