"""
NSE direct historical OHLCV fetcher.

Bypasses yfinance which is rate-limited on AWS EC2 IPs. Wraps the
jugaad-data library, which handles NSE's session/cookie/bot-detection
machinery and chunks multi-year requests automatically.

Output schema matches yfinance's `Ticker.history()`:
  DatetimeIndex (tz-naive, midnight)
  Columns: Open, High, Low, Close, Volume, Dividends, Stock Splits
"""
from __future__ import annotations

import logging
import os
from datetime import date, datetime, timedelta
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)

# jugaad-data caches inside ~/Library/Caches/ (mac) or ~/.cache/ (linux).
# Pre-create the path; jugaad's makedirs has a race that throws FileExistsError.
def _ensure_cache_dir() -> None:
    try:
        from appdirs import user_cache_dir
        d = user_cache_dir("nsehistory-stock")
        os.makedirs(d, exist_ok=True)
    except Exception:  # noqa: BLE001
        pass


def _to_datetime(d) -> datetime:
    if isinstance(d, datetime):
        return d
    if isinstance(d, date):
        return datetime.combine(d, datetime.min.time())
    return pd.to_datetime(d).to_pydatetime()


def fetch_history(
    symbol: str,
    years: int = 5,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
) -> pd.DataFrame:
    """
    Fetch daily OHLCV from NSE for `symbol` (no .NS suffix needed).

    Args:
        symbol: NSE symbol e.g. "RELIANCE"
        years: history window if `start` not provided
        start: explicit start datetime (overrides `years`)
        end: explicit end datetime (defaults to today)

    Returns:
        DataFrame with DatetimeIndex and yfinance-compatible columns.
        Empty DataFrame if NSE returns nothing or jugaad-data is unavailable.
    """
    end_dt = _to_datetime(end) if end else datetime.now()
    start_dt = _to_datetime(start) if start else (end_dt - timedelta(days=years * 365))

    try:
        _ensure_cache_dir()
        from jugaad_data.nse import stock_df
    except ImportError:
        logger.warning("jugaad-data not installed; NSE historical unavailable")
        return pd.DataFrame()

    try:
        raw = stock_df(
            symbol=symbol.upper(),
            from_date=start_dt.date(),
            to_date=end_dt.date(),
            series="EQ",
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(f"NSE historical fetch failed for {symbol}: {e}")
        return pd.DataFrame()

    if raw is None or raw.empty:
        logger.warning(f"NSE historical returned no data for {symbol}")
        return pd.DataFrame()

    # Map to yfinance schema
    df = raw.rename(columns={
        "DATE": "Date",
        "OPEN": "Open",
        "HIGH": "High",
        "LOW": "Low",
        "CLOSE": "Close",
        "VOLUME": "Volume",
    })
    df = df[["Date", "Open", "High", "Low", "Close", "Volume"]].copy()
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce").dt.normalize()
    df = df.dropna(subset=["Date"]).set_index("Date").sort_index()

    for col in ("Open", "High", "Low", "Close", "Volume"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["Open", "High", "Low", "Close"])

    # yfinance schema parity
    df["Dividends"] = 0.0
    df["Stock Splits"] = 0.0

    logger.info(
        f"NSE historical: {symbol} → {len(df)} rows "
        f"({df.index.min().date()} to {df.index.max().date()})"
    )
    return df
