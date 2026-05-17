"""Tests for Phase 4: strategist loop, leaderboard, rival snapshot."""
import json
import sqlite3
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock


# ── Leaderboard ───────────────────────────────────────────────────────────────

def _seed_trades(db_path: Path, pm_id: str, trades: list[dict]):
    with sqlite3.connect(db_path) as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pm_id TEXT, symbol TEXT, outcome TEXT,
            pnl_inr REAL, pnl_pct REAL,
            entry_date TEXT, exit_date TEXT
        )""")
        for t in trades:
            conn.execute(
                "INSERT INTO trades (pm_id, symbol, outcome, pnl_inr, pnl_pct, exit_date) VALUES (?,?,?,?,?,?)",
                (pm_id, t["symbol"], t["outcome"], t["pnl_inr"], t["pnl_pct"], t.get("exit_date", "2026-05-17")),
            )


def test_leaderboard_ranks_by_pnl(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    db = tmp_path / "paper_trades.db"
    _seed_trades(db, "1", [
        {"symbol": "RELIANCE", "outcome": "win", "pnl_inr": 500, "pnl_pct": 2.0},
        {"symbol": "TCS", "outcome": "loss", "pnl_inr": -100, "pnl_pct": -0.5},
    ])
    _seed_trades(db, "2", [
        {"symbol": "SBIN", "outcome": "win", "pnl_inr": 1200, "pnl_pct": 3.0},
    ])

    # Register PMs so list_pms works
    from common.core.pm_runtime import register_pm
    register_pm("1")
    register_pm("2")

    from common.leaderboard.snapshot import get_leaderboard
    board = get_leaderboard()
    assert len(board) == 2
    assert board[0]["pm_id"] == "2"  # PM2 has higher P&L
    assert board[0]["total_pnl"] == 1200.0
    assert board[1]["total_pnl"] == 400.0  # 500 - 100


def test_pm_stats_win_rate(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    db = tmp_path / "paper_trades.db"
    _seed_trades(db, "1", [
        {"symbol": "A", "outcome": "win", "pnl_inr": 100, "pnl_pct": 1.0},
        {"symbol": "B", "outcome": "win", "pnl_inr": 200, "pnl_pct": 2.0},
        {"symbol": "C", "outcome": "loss", "pnl_inr": -50, "pnl_pct": -0.5},
        {"symbol": "D", "outcome": "loss", "pnl_inr": -50, "pnl_pct": -0.5},
    ])
    from common.core.pm_runtime import register_pm
    register_pm("1")

    from common.leaderboard.snapshot import get_pm_stats
    stats = get_pm_stats("1")
    assert stats["n_trades"] == 4
    assert stats["win_rate_pct"] == 50.0
    assert stats["total_pnl"] == 200.0


def test_rival_snapshot(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    db = tmp_path / "paper_trades.db"
    _seed_trades(db, "2", [
        {"symbol": "SBIN", "outcome": "win", "pnl_inr": 300, "pnl_pct": 1.5},
    ])
    from common.core.pm_runtime import register_pm
    register_pm("1")
    register_pm("2")

    from common.leaderboard.snapshot import get_rival_snapshot
    snap = get_rival_snapshot("1", "2")
    assert snap["pm_id"] == "2"
    assert snap["total_pnl"] == 300.0
    assert len(snap["recent_trades"]) == 1


# ── Strategist loop ───────────────────────────────────────────────────────────

def test_strategist_cycle_do_nothing(tmp_path, monkeypatch):
    """Strategist cycle with LLM mocked to return DO_NOTHING."""
    monkeypatch.chdir(tmp_path)
    from common.core.pm_runtime import register_pm
    register_pm("test1")

    from common.strategist.loop import Strategist
    s = Strategist("test1")

    # Mock LLM to return DO_NOTHING
    mock_decision = {"action": "DO_NOTHING", "reasoning": "nothing to do", "details": {}}
    with patch.object(s, "_decide", return_value=mock_decision):
        result = s.run_cycle("test_trigger")

    assert result["action"] == "DO_NOTHING"
    assert result["trigger"] == "test_trigger"
    # Journal should have been updated
    journal_path = tmp_path / "pm_test1" / "state" / "journal.md"
    assert journal_path.exists()
    assert "DO_NOTHING" in journal_path.read_text()


def test_strategist_cycle_trade_publishes_event(tmp_path, monkeypatch):
    """TRADE action should publish exec_order event to the bus."""
    monkeypatch.chdir(tmp_path)
    # Point event bus to tmp_path
    import common.core.event_bus as _eb
    monkeypatch.setattr(_eb, "DB_PATH", tmp_path / "events.db")
    _eb._ensure_schema()

    from common.core.pm_runtime import register_pm
    register_pm("test2")

    from common.strategist.loop import Strategist
    from common.core.event_bus import get_bus

    s = Strategist("test2")
    mock_decision = {
        "action": "TRADE",
        "reasoning": "strong signal",
        "details": {"symbol": "RELIANCE", "direction": "BUY", "qty": 5, "sl": 1300.0},
    }
    with patch.object(s, "_decide", return_value=mock_decision):
        s.run_cycle("test_trade")

    # Check event was published
    bus = get_bus()
    events = bus.subscribe("exec_order.test2", since_id=0)
    assert len(events) == 1
    assert events[0]["payload"]["symbol"] == "RELIANCE"


def test_strategist_cycle_research_publishes_event(tmp_path, monkeypatch):
    """RESEARCH action should publish research event."""
    monkeypatch.chdir(tmp_path)
    import common.core.event_bus as _eb
    monkeypatch.setattr(_eb, "DB_PATH", tmp_path / "events.db")
    _eb._ensure_schema()

    from common.core.pm_runtime import register_pm
    register_pm("test3")

    from common.strategist.loop import Strategist
    from common.core.event_bus import get_bus

    s = Strategist("test3")
    mock_decision = {
        "action": "RESEARCH",
        "reasoning": "need more data",
        "details": {"question": "What is PM1's win rate on midcap stocks?", "priority": "high"},
    }
    with patch.object(s, "_decide", return_value=mock_decision):
        s.run_cycle("test_research")

    events = get_bus().subscribe("research.test3", since_id=0)
    assert len(events) == 1
    assert "midcap" in events[0]["payload"]["question"]


def test_strategist_cycle_evolve_commits_new_version(tmp_path, monkeypatch):
    """EVOLVE action should commit a new strategy version."""
    monkeypatch.chdir(tmp_path)
    from common.core.pm_runtime import register_pm
    from common.strategy.registry import get_active_version
    register_pm("test4")

    initial_version = get_active_version("test4")

    from common.strategist.loop import Strategist
    s = Strategist("test4")

    mock_decision = {
        "action": "EVOLVE",
        "reasoning": "try mean reversion",
        "details": {"hypothesis": "Switch from momentum to mean reversion on midcap stocks"},
    }

    # Mock the LLM inside _handle_evolve
    new_strategy = {"name": "mean_reversion", "watchlist": ["SBIN"], "gates": {}}
    with patch.object(s, "_decide", return_value=mock_decision), \
         patch("litellm.completion") as mock_llm:
        mock_resp = MagicMock()
        mock_resp.choices[0].message.content = json.dumps(new_strategy)
        mock_llm.return_value = mock_resp
        s.run_cycle("test_evolve")

    new_version = get_active_version("test4")
    assert new_version is not None
    assert new_version > (initial_version or 0)


def test_strategist_invalid_llm_action_defaults_to_do_nothing(tmp_path, monkeypatch):
    """If LLM returns an invalid action, default to DO_NOTHING."""
    monkeypatch.chdir(tmp_path)
    from common.core.pm_runtime import register_pm
    register_pm("test5")

    from common.strategist.loop import Strategist
    s = Strategist("test5")

    # LLM returns garbage
    with patch("litellm.completion") as mock_llm:
        mock_resp = MagicMock()
        mock_resp.choices[0].message.content = '{"action": "INVALID_ACTION", "reasoning": "bad"}'
        mock_llm.return_value = mock_resp
        result = s.run_cycle("test_invalid")

    assert result["action"] == "DO_NOTHING"


def test_strategist_llm_failure_defaults_to_do_nothing(tmp_path, monkeypatch):
    """If LLM throws, default to DO_NOTHING without crashing."""
    monkeypatch.chdir(tmp_path)
    from common.core.pm_runtime import register_pm
    register_pm("test6")

    from common.strategist.loop import Strategist
    s = Strategist("test6")

    with patch("litellm.completion", side_effect=Exception("Groq down")):
        result = s.run_cycle("test_llm_fail")

    assert result["action"] == "DO_NOTHING"
    assert "LLM unavailable" in result["reasoning"]
