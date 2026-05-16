"""Test for B13 — `weekly_analysis` should actually filter to the last 7 days."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path


def _seed_trades(db: Path) -> None:
    """Two trades: one closed 2 days ago (in window), one closed 30 days ago."""
    from agents.execution_agent import migrate_trades_schema
    migrate_trades_schema(db)
    now = datetime.now()
    recent = (now - timedelta(days=2)).isoformat()
    old = (now - timedelta(days=30)).isoformat()
    conn = sqlite3.connect(db)
    conn.executemany(
        "INSERT INTO trades (id, symbol, outcome, pnl_pct, pnl_inr, exit_date) "
        "VALUES (?, 'RELIANCE', ?, ?, ?, ?)",
        [
            ("recent", "win",  +2.5, 25.0, recent),
            ("old",    "loss", -3.0, -30.0, old),
        ],
    )
    conn.commit()
    conn.close()


def test_weekly_analysis_filters_to_last_7_days(tmp_path, monkeypatch):
    db = tmp_path / "paper_trades.db"
    monkeypatch.setattr("agents.execution_agent.DB_PATH", db)
    monkeypatch.setattr("agents.learning_agent.DB_PATH", db)

    _seed_trades(db)

    from agents.learning_agent import LearningAgent
    agent = LearningAgent({"trading": {"capital": 10_000}})
    out = agent.weekly_analysis("RELIANCE")

    # Only the recent trade (1 trade, 100% win) should be reflected.
    assert "Last 1 trades" in out, out
    assert "Win rate: 100%" in out, out
    # The old loss must NOT contribute.
    assert "Avg loss: +0.00%" in out or "Avg loss: -0.00%" in out, out


def test_weekly_analysis_handles_no_recent_trades(tmp_path, monkeypatch):
    db = tmp_path / "paper_trades.db"
    monkeypatch.setattr("agents.execution_agent.DB_PATH", db)
    monkeypatch.setattr("agents.learning_agent.DB_PATH", db)

    from agents.execution_agent import migrate_trades_schema
    migrate_trades_schema(db)
    # Single old trade, > 7 days
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO trades (id, symbol, outcome, pnl_pct, pnl_inr, exit_date) "
        "VALUES ('old', 'RELIANCE', 'win', 1.0, 10.0, ?)",
        ((datetime.now() - timedelta(days=30)).isoformat(),),
    )
    conn.commit()
    conn.close()

    from agents.learning_agent import LearningAgent
    agent = LearningAgent({"trading": {"capital": 10_000}})
    out = agent.weekly_analysis("RELIANCE")
    assert "no closed trades" in out.lower() or "no trade history" in out.lower(), out
