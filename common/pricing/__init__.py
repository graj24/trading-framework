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
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ticks (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol      TEXT NOT NULL,
            price       REAL NOT NULL,
            change_pct  REAL,
            ts          REAL NOT NULL   -- unix timestamp
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ticks_symbol_ts ON ticks(symbol, ts)")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS candles_5m (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol      TEXT NOT NULL,
            ts          REAL NOT NULL,  -- candle open time (unix)
            open        REAL NOT NULL,
            high        REAL NOT NULL,
            low         REAL NOT NULL,
            close       REAL NOT NULL,
            ticks       INTEGER DEFAULT 1,
            UNIQUE(symbol, ts)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_candles_symbol_ts ON candles_5m(symbol, ts)")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS candles_1d (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol      TEXT NOT NULL,
            date        TEXT NOT NULL,  -- YYYY-MM-DD IST
            open        REAL NOT NULL,
            high        REAL NOT NULL,
            low         REAL NOT NULL,
            close       REAL NOT NULL,
            prev_close  REAL,
            ticks       INTEGER DEFAULT 1,
            UNIQUE(symbol, date)
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
    """Write a fresh price: update snapshot, append tick, update candles."""
    change_pct = round((price - prev_close) / prev_close * 100, 2) if prev_close else 0.0
    now = time.time()
    sym = symbol.upper()
    try:
        conn = _get_conn()
        # 1. Update current snapshot
        conn.execute(
            "INSERT OR REPLACE INTO prices (symbol, price, prev_close, change_pct, updated_at) VALUES (?,?,?,?,?)",
            (sym, price, prev_close, change_pct, now)
        )
        # 2. Append tick (only during market hours to avoid filling DB with stale off-hours data)
        if is_market_open():
            conn.execute(
                "INSERT INTO ticks (symbol, price, change_pct, ts) VALUES (?,?,?,?)",
                (sym, price, change_pct, now)
            )
            # 3. Update 5-min candle (bucket = floor to nearest 5-min boundary)
            bucket = now - (now % 300)
            conn.execute("""
                INSERT INTO candles_5m (symbol, ts, open, high, low, close, ticks)
                VALUES (?, ?, ?, ?, ?, ?, 1)
                ON CONFLICT(symbol, ts) DO UPDATE SET
                    high  = MAX(high, excluded.high),
                    low   = MIN(low, excluded.low),
                    close = excluded.close,
                    ticks = ticks + 1
            """, (sym, bucket, price, price, price, price))
            # 4. Update daily candle
            date_ist = datetime.now(IST).strftime("%Y-%m-%d")
            conn.execute("""
                INSERT INTO candles_1d (symbol, date, open, high, low, close, prev_close, ticks)
                VALUES (?, ?, ?, ?, ?, ?, ?, 1)
                ON CONFLICT(symbol, date) DO UPDATE SET
                    high      = MAX(high, excluded.high),
                    low       = MIN(low, excluded.low),
                    close     = excluded.close,
                    ticks     = ticks + 1
            """, (sym, date_ist, price, price, price, price, prev_close))
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


# ── Candle query helpers (used by PMs, scanner, backtester) ──────────────────

def get_candles_5m(symbol: str, limit: int = 78) -> list[dict]:
    """Return last N 5-min candles for a symbol (78 = full trading day)."""
    try:
        conn = _get_conn()
        rows = conn.execute(
            "SELECT ts, open, high, low, close, ticks FROM candles_5m "
            "WHERE symbol=? ORDER BY ts DESC LIMIT ?",
            (symbol.upper(), limit)
        ).fetchall()
        conn.close()
        return [{"ts": r[0], "open": r[1], "high": r[2], "low": r[3],
                 "close": r[4], "ticks": r[5]} for r in reversed(rows)]
    except Exception as e:
        logger.debug(f"get_candles_5m error: {e}")
        return []


def get_candles_1d(symbol: str, limit: int = 252) -> list[dict]:
    """Return last N daily candles for a symbol (252 = ~1 trading year)."""
    try:
        conn = _get_conn()
        rows = conn.execute(
            "SELECT date, open, high, low, close, prev_close, ticks FROM candles_1d "
            "WHERE symbol=? ORDER BY date DESC LIMIT ?",
            (symbol.upper(), limit)
        ).fetchall()
        conn.close()
        return [{"date": r[0], "open": r[1], "high": r[2], "low": r[3],
                 "close": r[4], "prev_close": r[5], "ticks": r[6]} for r in reversed(rows)]
    except Exception as e:
        logger.debug(f"get_candles_1d error: {e}")
        return []


def get_db_stats() -> dict:
    """Return row counts for monitoring."""
    try:
        conn = _get_conn()
        return {
            "prices":     conn.execute("SELECT COUNT(*) FROM prices").fetchone()[0],
            "ticks":      conn.execute("SELECT COUNT(*) FROM ticks").fetchone()[0],
            "candles_5m": conn.execute("SELECT COUNT(*) FROM candles_5m").fetchone()[0],
            "candles_1d": conn.execute("SELECT COUNT(*) FROM candles_1d").fetchone()[0],
        }
    except Exception as e:
        logger.debug(f"get_db_stats error: {e}")
        return {}
