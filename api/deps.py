"""Shared dependencies for FastAPI routers."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from core.config import get_config

DB_PATH = Path(__file__).parent.parent / "paper_trades.db"


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def get_cfg() -> dict:
    return get_config()
