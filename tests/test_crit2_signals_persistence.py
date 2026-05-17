"""Tests for CRIT-2 — trades schema migration + signals_at_entry plumbing.

The bug:
* The `trades` table never stored entry-time signals.
* `main.py` read `t.get("technical_score", 0)` which always returned 0
  (and crashed before CRIT-1 was fixed).

The fix:
1. Add 5 nullable signal columns + a `weights_applied` flag to `trades`.
2. Make the migration idempotent (re-run safely).
3. Extend `ExecutionAgent.execute_trade` to accept a `signals_at_entry`
   dict and persist it.
4. Update `main.py`/learning loop to use `weights_applied=0` filter and
   set `1` after applying weights (covers Issue B2 too).
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest


# ── Migration helper ────────────────────────────────────────────────────────

def _new_legacy_db(tmp_path: Path) -> Path:
    """Create a DB with the OLD schema (pre-CRIT-2) and one closed trade,
    so we can verify the migration preserves data."""
    p = tmp_path / "paper_trades.db"
    conn = sqlite3.connect(p)
    conn.execute(
        """CREATE TABLE trades (
            id            TEXT PRIMARY KEY,
            symbol        TEXT NOT NULL,
            entry_date    TEXT,
            entry_price   REAL,
            stop_loss     REAL,
            target        REAL,
            position_size REAL,
            exit_date     TEXT,
            exit_price    REAL,
            pnl_pct       REAL,
            pnl_inr       REAL,
            outcome       TEXT DEFAULT 'open',
            reasoning     TEXT,
            created_at    TEXT
        )"""
    )
    conn.execute(
        "INSERT INTO trades (id, symbol, outcome, pnl_inr) VALUES "
        "('tr1', 'RELIANCE', 'loss', -10.5)"
    )
    conn.commit()
    conn.close()
    return p


def test_migration_adds_columns_idempotently(tmp_path):
    from agents.execution_agent import migrate_trades_schema

    db = _new_legacy_db(tmp_path)
    migrate_trades_schema(db)
    migrate_trades_schema(db)  # 2nd call must not raise

    conn = sqlite3.connect(db)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(trades)").fetchall()}
    for c in (
        "technical_score", "sentiment", "pattern_ev",
        "sector_momentum", "regime_alignment", "weights_applied",
    ):
        assert c in cols, f"column {c!r} missing after migration"

    # Existing row preserved.
    rows = conn.execute("SELECT id, symbol, outcome, pnl_inr FROM trades").fetchall()
    assert rows == [("tr1", "RELIANCE", "loss", -10.5)]
    conn.close()


def test_migration_creates_table_when_db_is_empty(tmp_path):
    """Migration must also handle the fresh-install case where the DB
    file may not exist yet."""
    from agents.execution_agent import migrate_trades_schema

    db = tmp_path / "fresh.db"
    migrate_trades_schema(db)

    conn = sqlite3.connect(db)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(trades)").fetchall()}
    assert "technical_score" in cols
    assert "weights_applied" in cols
    conn.close()


# ── execute_trade now persists signals ──────────────────────────────────────

def test_execute_trade_persists_signals_at_entry(tmp_path, monkeypatch):
    """ExecutionAgent.execute_trade must accept signals_at_entry and write
    each value into the corresponding column."""
    db = tmp_path / "paper_trades.db"
    monkeypatch.setattr("agents.execution_agent.DB_PATH", db)

    from agents.execution_agent import ExecutionAgent, migrate_trades_schema

    migrate_trades_schema(db)  # ensure schema before the agent constructs.

    agent = ExecutionAgent({"trading": {"mode": "paper"}})
    agent.execute_trade(
        symbol="RELIANCE",
        entry_price=100.0,
        stop_loss=99.0,
        target=103.0,
        position_size=1500.0,
        reasoning="test",
        signals_at_entry={
            "technical_score": 7,
            "sentiment": 0.42,
            "pattern_ev": 1.5,
            "sector_momentum": 0.3,
            "regime_alignment": 0.8,
        },
    )

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM trades WHERE symbol='RELIANCE'").fetchone()
    assert row["technical_score"] == 7
    assert abs(row["sentiment"] - 0.42) < 1e-9
    assert abs(row["pattern_ev"] - 1.5) < 1e-9
    assert abs(row["sector_momentum"] - 0.3) < 1e-9
    assert abs(row["regime_alignment"] - 0.8) < 1e-9
    assert row["weights_applied"] == 0
    conn.close()


def test_execute_trade_works_without_signals_at_entry(tmp_path, monkeypatch):
    """Back-compat: the param is optional. Existing call sites that don't
    pass signals must still work and store NULL signals."""
    db = tmp_path / "paper_trades.db"
    monkeypatch.setattr("agents.execution_agent.DB_PATH", db)

    from agents.execution_agent import ExecutionAgent, migrate_trades_schema

    migrate_trades_schema(db)
    agent = ExecutionAgent({"trading": {"mode": "paper"}})

    agent.execute_trade(
        symbol="TCS", entry_price=100.0, stop_loss=99.0,
        target=103.0, position_size=1000.0, reasoning="back-compat",
    )

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM trades WHERE symbol='TCS'").fetchone()
    assert row["technical_score"] is None
    assert row["weights_applied"] == 0
    conn.close()


# ── weights_applied flag prevents re-application ────────────────────────────

def test_weights_applied_filter_only_new_trades(tmp_path, monkeypatch):
    """A helper to fetch trades that still need weight application must
    only return rows where weights_applied = 0."""
    db = tmp_path / "paper_trades.db"
    monkeypatch.setattr("agents.execution_agent.DB_PATH", db)

    from agents.execution_agent import (
        ExecutionAgent, migrate_trades_schema, fetch_unweighted_closed_trades,
        mark_trade_weights_applied,
    )

    migrate_trades_schema(db)
    agent = ExecutionAgent({"trading": {"mode": "paper"}})

    # Two trades, both will be force-closed below.
    agent.execute_trade(symbol="A", entry_price=100, stop_loss=99,
                        target=103, position_size=1000, reasoning="x",
                        signals_at_entry={"technical_score": 6})
    agent.execute_trade(symbol="B", entry_price=100, stop_loss=99,
                        target=103, position_size=1000, reasoning="y",
                        signals_at_entry={"technical_score": 6})

    # Force-close both as wins.
    conn = sqlite3.connect(db)
    conn.execute("UPDATE trades SET outcome='win', pnl_inr=10.0")
    conn.commit()
    conn.close()

    # First call: both trades returned.
    trades = fetch_unweighted_closed_trades(db)
    assert {t["symbol"] for t in trades} == {"A", "B"}

    # Mark trade A as applied; only B remains.
    mark_trade_weights_applied(db, trades[0]["id"])
    remaining = fetch_unweighted_closed_trades(db)
    assert len(remaining) == 1
    assert remaining[0]["symbol"] != trades[0]["symbol"]
