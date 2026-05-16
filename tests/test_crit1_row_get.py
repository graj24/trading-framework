"""Regression tests for CRIT-1 — sqlite3.Row .get() crash in main.py learning loop.

Documented in docs-verification/findings.md.

The bug: sqlite3.Row does not implement .get(), so any code path that does
`row.get("col", default)` crashes with AttributeError as soon as a closed
trade exists in paper_trades.db.

The fix: read columns through `_row_get(row, key, default)` (a small helper
in `core.row_utils`) which handles both dict-like and sqlite3.Row inputs.
"""
from __future__ import annotations

import sqlite3
from typing import Any

import pytest

from core.row_utils import row_get


# ── Failing case from production ─────────────────────────────────────────────

def _make_row(**cols: Any) -> sqlite3.Row:
    """Return a sqlite3.Row populated with the given columns."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    placeholders = ", ".join(f"? AS {k}" for k in cols.keys())
    return conn.execute(f"SELECT {placeholders}", tuple(cols.values())).fetchone()


def test_row_get_returns_value_when_column_present():
    row = _make_row(symbol="RELIANCE", pnl_inr=12.5)
    assert row_get(row, "symbol", "?") == "RELIANCE"
    assert row_get(row, "pnl_inr", 0) == 12.5


def test_row_get_returns_default_when_column_missing():
    row = _make_row(symbol="RELIANCE")
    # The bug we're fixing: the column doesn't exist on the Row.
    assert row_get(row, "technical_score", 0) == 0
    assert row_get(row, "sentiment", 0.0) == 0.0


def test_row_get_handles_plain_dict():
    """The helper must also work on dicts so test fixtures stay simple."""
    d = {"a": 1, "b": "x"}
    assert row_get(d, "a", -1) == 1
    assert row_get(d, "missing", "fallback") == "fallback"


def test_row_get_returns_default_on_none_input():
    """Defensive: callers that pass None shouldn't crash."""
    assert row_get(None, "anything", "default") == "default"


# ── Reproduce the original AttributeError ───────────────────────────────────

def test_sqlite3_row_does_not_have_get_method():
    """Documents WHY this helper exists. If sqlite3.Row ever grows .get(),
    we can simplify, but this assertion guards against silent regression."""
    row = _make_row(x=1)
    with pytest.raises(AttributeError):
        row.get("x", 0)  # type: ignore[attr-defined]


# ── End-to-end: the main.py learning loop pattern ────────────────────────────

def test_main_py_loop_pattern_no_longer_crashes():
    """Mirror the exact dict construction in main.py:135-138 — but using
    row_get instead of row.get()."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE trades (
            id TEXT, symbol TEXT, pnl_inr REAL, outcome TEXT
        )"""
    )
    conn.execute("INSERT INTO trades VALUES ('a','RELIANCE',12.0,'win')")
    conn.execute("INSERT INTO trades VALUES ('b','TCS',-5.0,'loss')")
    rows = conn.execute("SELECT * FROM trades WHERE outcome != 'open'").fetchall()
    assert len(rows) == 2

    # This loop body must not raise.
    payloads = []
    for t in rows:
        payloads.append(
            {
                "technical_score": row_get(t, "technical_score", 0),
                "news_sentiment":  row_get(t, "sentiment", 0),
                "pattern_ev":      row_get(t, "pattern_ev", 0),
            }
        )
    assert all(p["technical_score"] == 0 for p in payloads)
