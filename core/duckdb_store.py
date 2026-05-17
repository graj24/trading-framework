"""core/duckdb_store.py — DuckDB query layer over per-stock parquet files.

P2 §22: provides fast SQL queries over the existing parquet layout without
migrating any data.  DuckDB reads parquet files directly — no ETL needed.

Key helpers
-----------
symbol_history(symbol, start, end)  → pd.DataFrame  (daily OHLCV)
market_data(start, end)             → pd.DataFrame  (sector indices)
multi_symbol(symbols, start, end)   → pd.DataFrame  (stacked OHLCV)
query(sql)                          → pd.DataFrame  (raw SQL, for power users)

All functions return empty DataFrames gracefully when data is missing.
"""
from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path
from typing import Sequence

import pandas as pd

logger = logging.getLogger(__name__)

from core.knowledge_base import STOCKS_DIR

MARKET_FILE = STOCKS_DIR / "_market_data.parquet"


def _conn():
    """Return a fresh in-memory DuckDB connection with parquet support."""
    import duckdb
    return duckdb.connect(":memory:")


# ── Public API ────────────────────────────────────────────────────────────────

@lru_cache(maxsize=256)
def _index_col(path: Path) -> str:
    """Return the name of the index column in a parquet file."""
    try:
        import duckdb
        con  = duckdb.connect(":memory:")
        # parquet_schema returns (file, name, type, ...) — name is index 1
        rows = con.execute(f"SELECT * FROM parquet_schema('{path}')").fetchall()
        con.close()
        names = [r[1] for r in rows if r[1] not in ("schema",)]
        for candidate in ("index", "__index_level_0__"):
            if candidate in names:
                return candidate
        return names[0] if names else "index"
    except Exception:
        return "index"


def symbol_history(
    symbol: str,
    start: str | None = None,
    end:   str | None = None,
    columns: Sequence[str] | None = None,
) -> pd.DataFrame:
    """Return daily OHLCV for *symbol* between *start* and *end* (ISO dates).

    Args:
        symbol:  NSE ticker (e.g. "RELIANCE").
        start:   inclusive lower bound, e.g. "2024-01-01". None = no lower bound.
        end:     inclusive upper bound, e.g. "2024-12-31". None = no upper bound.
        columns: subset of columns to return. None = all.

    Returns:
        DataFrame indexed by date, or empty DataFrame if data is missing.
    """
    path = STOCKS_DIR / symbol / "price_history.parquet"
    if not path.exists():
        logger.debug("symbol_history: no parquet for %s", symbol)
        return pd.DataFrame()

    col_clause = ", ".join(columns) if columns else "*"
    idx_col = _index_col(path)
    where = _date_where(idx_col, start, end)
    sql = f"SELECT {col_clause} FROM read_parquet('{path}'){where} ORDER BY {idx_col}"

    try:
        con = _conn()
        df  = con.execute(sql).df()
        con.close()
        # Normalise index column name
        for cname in ("index", "__index_level_0__"):
            if cname in df.columns:
                df[cname] = pd.to_datetime(df[cname], utc=True).dt.tz_localize(None)
                df = df.set_index(cname)
                df.index.name = None
                break
        return df
    except Exception as e:
        logger.warning("symbol_history(%s) query failed: %s", symbol, e)
        return pd.DataFrame()


def market_data(
    start: str | None = None,
    end:   str | None = None,
) -> pd.DataFrame:
    """Return the cached sector-index data (NIFTY, BankNifty, VIX, etc.).

    The file is written by ``ml_model.load_market_data``.  Returns empty
    DataFrame if the cache hasn't been built yet.
    """
    if not MARKET_FILE.exists():
        logger.debug("market_data: cache file not found at %s", MARKET_FILE)
        return pd.DataFrame()

    idx_col = _index_col(MARKET_FILE)
    where = _date_where(idx_col, start, end)
    sql = f"SELECT * FROM read_parquet('{MARKET_FILE}'){where} ORDER BY {idx_col}"

    try:
        con = _conn()
        df  = con.execute(sql).df()
        con.close()
        for cname in ("index", "__index_level_0__"):
            if cname in df.columns:
                df[cname] = pd.to_datetime(df[cname], utc=True).dt.tz_localize(None)
                df = df.set_index(cname)
                df.index.name = None
                break
        return df
    except Exception as e:
        logger.warning("market_data query failed: %s", e)
        return pd.DataFrame()


def multi_symbol(
    symbols: Sequence[str],
    start: str | None = None,
    end:   str | None = None,
    columns: Sequence[str] | None = ("Open", "High", "Low", "Close", "Volume"),
) -> pd.DataFrame:
    """Return stacked OHLCV for multiple symbols with a 'symbol' column.

    Useful for cross-sectional analysis (e.g. sector rotation).
    """
    frames = []
    for sym in symbols:
        df = symbol_history(sym, start, end, columns=list(columns) if columns else None)
        if not df.empty:
            df = df.copy()
            df["symbol"] = sym
            frames.append(df)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames).sort_index()


def query(sql: str) -> pd.DataFrame:
    """Execute arbitrary DuckDB SQL and return a DataFrame.

    The caller is responsible for referencing parquet files via
    ``read_parquet('stocks/SYMBOL/price_history.parquet')`` in the SQL.
    """
    try:
        con = _conn()
        df  = con.execute(sql).df()
        con.close()
        return df
    except Exception as e:
        logger.warning("duckdb_store.query failed: %s", e)
        return pd.DataFrame()


# ── Internal helpers ──────────────────────────────────────────────────────────

def _validate_date(value: str) -> None:
    """Raise ValueError if *value* is not a plain YYYY-MM-DD string."""
    import re
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError(f"Invalid date string (expected YYYY-MM-DD): {value!r}")


def _date_where(col: str, start: str | None, end: str | None) -> str:
    parts = []
    if start:
        _validate_date(start)
        parts.append(f"CAST({col} AS DATE) >= DATE '{start}'")
    if end:
        _validate_date(end)
        parts.append(f"CAST({col} AS DATE) <= DATE '{end}'")
    return (" WHERE " + " AND ".join(parts)) if parts else ""
