"""
Central price cache — single source of truth for all NSE prices.

Architecture:
  price-feed daemon  →  prices.db  ←  all consumers (ws, tools, scheduler, api)

Only the price-feed daemon ever calls NSE. Everyone else reads prices.db.
"""
from __future__ import annotations

import logging
import sqlite3
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

try:
    from zoneinfo import ZoneInfo
    IST = ZoneInfo("Asia/Kolkata")
except ImportError:
    IST = timezone(timedelta(hours=5, minutes=30))

_DB_PATH = Path("prices.db")
_STALE_SECONDS = 120  # data older than 2 min is considered stale


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(_DB_PATH, check_same_thread=False)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS prices (
            symbol      TEXT PRIMARY KEY,
            price       REAL NOT NULL,
            prev_close  REAL,
            change_pct  REAL,
            updated_at  REAL NOT NULL  -- unix timestamp
        )
    """)
    conn.commit()
    return conn


# ── Read API (used by all consumers) ─────────────────────────────────────────

def get(symbol: str) -> Optional[dict]:
    """Return cached price dict or None if missing/stale."""
    try:
        conn = _get_conn()
        row = conn.execute(
            "SELECT price, prev_close, change_pct, updated_at FROM prices WHERE symbol = ?",
            (symbol.upper(),)
        ).fetchone()
        conn.close()
        if not row:
            return None
        price, prev_close, change_pct, updated_at = row
        age = time.time() - updated_at
        return {
            "symbol": symbol.upper(),
            "price": price,
            "prev_close": prev_close,
            "change_pct": change_pct,
            "age_seconds": round(age),
            "stale": age > _STALE_SECONDS,
        }
    except Exception as e:
        logger.debug(f"price cache read error: {e}")
        return None


def get_many(symbols: list[str]) -> dict[str, dict]:
    """Return {symbol: price_dict} for all cached symbols. Missing ones omitted."""
    if not symbols:
        return {}
    try:
        conn = _get_conn()
        placeholders = ",".join("?" * len(symbols))
        rows = conn.execute(
            f"SELECT symbol, price, prev_close, change_pct, updated_at FROM prices WHERE symbol IN ({placeholders})",
            [s.upper() for s in symbols]
        ).fetchall()
        conn.close()
        now = time.time()
        return {
            row[0]: {
                "symbol": row[0],
                "price": row[1],
                "prev_close": row[2],
                "change_pct": row[3],
                "age_seconds": round(now - row[4]),
                "stale": (now - row[4]) > _STALE_SECONDS,
            }
            for row in rows
        }
    except Exception as e:
        logger.debug(f"price cache read_many error: {e}")
        return {}


def get_all() -> dict[str, dict]:
    """Return all cached prices."""
    try:
        conn = _get_conn()
        rows = conn.execute(
            "SELECT symbol, price, prev_close, change_pct, updated_at FROM prices"
        ).fetchall()
        conn.close()
        now = time.time()
        return {
            row[0]: {
                "symbol": row[0],
                "price": row[1],
                "prev_close": row[2],
                "change_pct": row[3],
                "age_seconds": round(now - row[4]),
                "stale": (now - row[4]) > _STALE_SECONDS,
            }
            for row in rows
        }
    except Exception as e:
        logger.debug(f"price cache get_all error: {e}")
        return {}


# ── Write API (used only by price-feed daemon) ────────────────────────────────

def upsert(symbol: str, price: float, prev_close: float) -> None:
    """Write a fresh price into the cache."""
    change_pct = round((price - prev_close) / prev_close * 100, 2) if prev_close else 0.0
    try:
        conn = _get_conn()
        conn.execute(
            "INSERT OR REPLACE INTO prices (symbol, price, prev_close, change_pct, updated_at) VALUES (?,?,?,?,?)",
            (symbol.upper(), price, prev_close, change_pct, time.time())
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.warning(f"price cache upsert error: {e}")


# ── Market hours helper ───────────────────────────────────────────────────────

def is_market_open() -> bool:
    now = datetime.now(IST)
    if now.weekday() >= 5:  # Saturday=5, Sunday=6
        return False
    open_t = now.replace(hour=9, minute=15, second=0, microsecond=0)
    close_t = now.replace(hour=15, minute=30, second=0, microsecond=0)
    return open_t <= now <= close_t


def poll_interval_seconds() -> int:
    """How often the feed daemon should poll NSE."""
    return 30 if is_market_open() else 300  # 30s in market, 5 min out
