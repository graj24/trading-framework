"""
Schema migrations for paper_trades.db.

Idempotent — safe to call repeatedly on startup. Adds columns that newer code
expects without breaking older trades data.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
DB_PATH = BASE / "paper_trades.db"


def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    cur = conn.execute(f"PRAGMA table_info({table})")
    return any(row[1] == column for row in cur.fetchall())


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    )
    return cur.fetchone() is not None


def ensure_paper_trades_schema(db_path: Path | str = DB_PATH) -> None:
    """
    Ensure paper_trades.db has all columns that the multi-PM runtime expects:
      - pm_id        TEXT default '1'     (which PM owns the trade)

    Existing rows get pm_id='1' so PM1 inherits the historical book.
    """
    db = Path(db_path)
    if not db.exists():
        return  # nothing to migrate; db will be created with correct schema by caller

    with sqlite3.connect(db) as conn:
        if not _table_exists(conn, "trades"):
            return
        if not _column_exists(conn, "trades", "pm_id"):
            conn.execute("ALTER TABLE trades ADD COLUMN pm_id TEXT DEFAULT '1'")
            conn.execute("UPDATE trades SET pm_id='1' WHERE pm_id IS NULL")
            conn.commit()


# Run on import so any module that touches paper_trades.db is safe.
try:
    ensure_paper_trades_schema()
except Exception:
    # Don't crash the app on a migration error — just continue and let queries surface issues.
    pass
