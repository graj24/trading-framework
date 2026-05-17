"""Tests for C.3 — ExecutionAgent.execute_trade routes through Broker in live mode."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    db = tmp_path / "paper_trades.db"
    monkeypatch.setattr("agents.execution_agent.DB_PATH", db)
    return db


def test_paper_mode_does_not_call_broker(isolated_db, monkeypatch):
    """Existing behaviour preserved: paper mode never touches the Broker."""
    from agents.execution_agent import ExecutionAgent

    broker_called = {"n": 0}

    class _FailingBroker:
        def place_order(self, **kw):
            broker_called["n"] += 1
            raise RuntimeError("broker should not have been called")

    monkeypatch.setattr("core.broker.get_broker", lambda cfg: _FailingBroker())

    agent = ExecutionAgent({"trading": {"mode": "paper", "capital": 10_000}})
    out = agent.execute_trade(
        symbol="RELIANCE", entry_price=100, stop_loss=99, target=103,
        position_size=1500, reasoning="paper",
    )
    assert "broker_order_id" not in out
    assert broker_called["n"] == 0


def test_live_mode_calls_broker_and_logs_trade(isolated_db, monkeypatch):
    """Live mode places a real order AND records it in SQLite."""
    from agents.execution_agent import ExecutionAgent

    placed: dict = {}

    class _StubBroker:
        def place_order(self, **kw):
            placed.update(kw)
            return "order-42"

    monkeypatch.setattr("core.broker.get_broker", lambda cfg: _StubBroker())

    agent = ExecutionAgent({"trading": {"mode": "live", "capital": 10_000}})
    out = agent.execute_trade(
        symbol="RELIANCE", entry_price=1000, stop_loss=990, target=1020,
        position_size=10_000, reasoning="live test",
    )
    assert out["broker_order_id"] == "order-42"
    assert placed["symbol"] == "RELIANCE"
    assert placed["qty"] >= 1
    assert placed["sl"] == 990

    # SQLite row must also exist.
    conn = sqlite3.connect(isolated_db)
    rows = conn.execute("SELECT symbol FROM trades WHERE outcome='open'").fetchall()
    conn.close()
    assert rows == [("RELIANCE",)]


def test_live_mode_propagates_broker_failure(isolated_db, monkeypatch):
    """If the broker raises, execute_trade must propagate (and NOT have
    written a stranded SQLite row)."""
    from agents.execution_agent import ExecutionAgent

    class _BoomBroker:
        def place_order(self, **kw):
            raise RuntimeError("broker exploded")

    monkeypatch.setattr("core.broker.get_broker", lambda cfg: _BoomBroker())

    agent = ExecutionAgent({"trading": {"mode": "live", "capital": 10_000}})
    with pytest.raises(RuntimeError):
        agent.execute_trade(
            symbol="RELIANCE", entry_price=1000, stop_loss=990, target=1020,
            position_size=10_000, reasoning="should fail",
        )
    conn = sqlite3.connect(isolated_db)
    rows = conn.execute("SELECT * FROM trades").fetchall()
    conn.close()
    assert rows == [], "no stranded SQLite row when broker fails"
