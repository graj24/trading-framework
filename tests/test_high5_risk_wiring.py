"""Tests for HIGH-5 — RiskManager wired with real open positions / daily P&L."""
from __future__ import annotations

import sqlite3
import sys
import types
from datetime import date, datetime
from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def _stub_heavy_imports(monkeypatch):
    """Avoid pulling FinBERT / transformers when importing agents.master."""
    fake_pipe = MagicMock(return_value=[[{"label": "POSITIVE", "score": 0.5}]])
    fake_transformers = types.ModuleType("transformers")
    fake_transformers.pipeline = lambda *a, **kw: fake_pipe  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "transformers", fake_transformers)


@pytest.fixture
def db_with_trades(tmp_path, monkeypatch):
    """Create a paper_trades.db with two open trades (RELIANCE, INFY) and
    one closed trade today with -50 INR P&L."""
    db = tmp_path / "paper_trades.db"
    monkeypatch.setattr("agents.execution_agent.DB_PATH", db)

    from agents.execution_agent import migrate_trades_schema
    migrate_trades_schema(db)

    today = date.today().isoformat()
    conn = sqlite3.connect(db)
    conn.executemany(
        "INSERT INTO trades (id, symbol, outcome, pnl_inr, exit_date) VALUES (?, ?, ?, ?, ?)",
        [
            ("o1", "RELIANCE", "open", None, None),
            ("o2", "INFY", "open", None, None),
            ("c1", "TCS", "loss", -50.0, f"{today}T15:30:00"),
        ],
    )
    conn.commit()
    conn.close()
    return db


def test_get_open_position_symbols_returns_open_only(db_with_trades):
    from agents.execution_agent import get_open_position_symbols

    syms = get_open_position_symbols(db_with_trades)
    assert sorted(syms) == ["INFY", "RELIANCE"]


def test_get_open_position_symbols_empty_db(tmp_path):
    """Helper must not crash when the DB doesn't exist or has no trades."""
    from agents.execution_agent import get_open_position_symbols, migrate_trades_schema

    db = tmp_path / "fresh.db"
    migrate_trades_schema(db)
    assert get_open_position_symbols(db) == []


def test_today_pnl_pct_sums_only_today_closed(db_with_trades):
    from agents.execution_agent import today_pnl_pct

    # Capital 10_000; today's closed P&L = -50 INR → -0.5 %.
    assert abs(today_pnl_pct(10_000, db_path=db_with_trades) - (-0.5)) < 1e-9


def test_today_pnl_pct_ignores_yesterday_trades(tmp_path, monkeypatch):
    from agents.execution_agent import today_pnl_pct, migrate_trades_schema

    db = tmp_path / "paper_trades.db"
    monkeypatch.setattr("agents.execution_agent.DB_PATH", db)
    migrate_trades_schema(db)
    yesterday_iso = "2024-01-01T15:30:00"  # definitely not today
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO trades (id, symbol, outcome, pnl_inr, exit_date) VALUES "
        "('y1', 'RELIANCE', 'win', 100.0, ?)",
        (yesterday_iso,),
    )
    conn.commit()
    conn.close()

    assert today_pnl_pct(10_000, db_path=db) == 0.0


def test_today_pnl_pct_zero_capital(db_with_trades):
    """Defensive: avoid division-by-zero."""
    from agents.execution_agent import today_pnl_pct
    assert today_pnl_pct(0, db_path=db_with_trades) == 0.0


# ── Wiring through to MasterAgent.run_for_stock ─────────────────────────────

def test_master_passes_real_open_positions_and_pnl_to_risk_manager(monkeypatch, tmp_path):
    """MasterAgent.run_for_stock must compute open_positions + daily_pnl_pct
    from the trade ledger before calling RiskManager."""
    db = tmp_path / "paper_trades.db"
    monkeypatch.setattr("agents.execution_agent.DB_PATH", db)

    from agents.execution_agent import migrate_trades_schema
    migrate_trades_schema(db)

    today = date.today().isoformat()
    conn = sqlite3.connect(db)
    conn.executemany(
        "INSERT INTO trades (id, symbol, outcome, pnl_inr, exit_date) VALUES (?, ?, ?, ?, ?)",
        [
            ("o1", "INFY", "open", None, None),
            ("c1", "TCS", "loss", -200.0, f"{today}T12:00:00"),
        ],
    )
    conn.commit()
    conn.close()

    # Build a stub RiskManager to capture what MasterAgent passes to it.
    captured: dict = {}

    class _StubRM:
        def run(self, ctx):
            captured["ctx"] = ctx
            from agents.base import AgentResult, AgentStatus
            return AgentResult(
                "stub", AgentStatus.DONE,
                data={"allowed": True, "position_size": 100.0,
                      "stop_loss": 99.0, "reason": "stub"},
            )

    # Build a MasterAgent and monkey-patch sub-agents to no-ops + the
    # risk manager to our stub.
    from agents.base import AgentResult, AgentStatus

    class _StubAgent:
        def run(self, _ctx=None):
            return AgentResult(
                "stub", AgentStatus.DONE,
                data={"current_price": 100.0, "trend": "up",
                      "macd_signal": "bullish", "volume_ratio": 1.5,
                      "technical_score": 8, "sentiment": 0.4,
                      "pattern_ev": 1.0, "win_rate": 60,
                      "regime": "trending_bull", "rsi": 55,
                      "expected_value": 1.0, "avg_win": 2.0, "avg_loss": -1.0},
            )

    from agents.master import MasterAgent
    config = {
        "trading": {"capital": 10_000, "mode": "paper"},
        "llm": {"model": "x"},
        "watchlist": [],
    }
    master = MasterAgent(config)
    master.technical_agent = _StubAgent()
    master.news_agent = _StubAgent()
    master.pattern_agent = _StubAgent()
    master.regime_agent = _StubAgent()
    master.risk_manager = _StubRM()

    # Force LLM to fall back to the rule-based path → BUY route triggers
    # risk manager.
    monkeypatch.setattr(
        "agents.master._llm_decision",
        lambda *a, **kw: {"decision": "BUY", "confidence": 80, "entry": 100,
                          "stop_loss": 99, "target": 103, "reasoning": "stub"},
    )

    result = master.run_for_stock("RELIANCE")
    assert result.ok()
    ctx = captured.get("ctx")
    assert ctx is not None, "RiskManager not invoked"
    # Open positions should now contain INFY (from the DB), not be empty.
    assert "INFY" in ctx["open_positions"], ctx
    # Today's P&L: -200 INR / 10000 capital * 100 = -2.0%.
    assert abs(ctx["daily_pnl_pct"] - (-2.0)) < 1e-9, ctx["daily_pnl_pct"]
