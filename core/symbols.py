"""Canonical NSE symbol definitions for the trading framework.

Until 2026-05-16 the NIFTY 50 list was duplicated across at least three
files (`india_intraday_model.py`, `agents/intraday_scanner.py`,
`fetch_universe.py`) with subtle differences (`BAJAJ-AUTO` vs
`BAJAJ_AUTO`, ordering, even membership). This module is now the single
source of truth.

Two normalisations exist in the wild:
* **NSE root**: ``BAJAJ-AUTO`` — used by NSE / Groww / Kite (live data + orders).
* **Filesystem-safe**: ``BAJAJ_AUTO`` — used as directory names in
  ``stocks/`` and ``stocks_1h/`` (because Yahoo's `.NS` ticker has its own
  variant ``BAJAJ-AUTO.NS`` that we strip).

The helpers below convert between them.
"""
from __future__ import annotations

# Canonical NIFTY 50 symbols in NSE-root form (BAJAJ-AUTO, M&M, etc.).
NIFTY_50: tuple[str, ...] = (
    "RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK",
    "HINDUNILVR", "SBIN", "BHARTIARTL", "ITC", "KOTAKBANK",
    "LT", "AXISBANK", "ASIANPAINT", "MARUTI", "TITAN",
    "SUNPHARMA", "ULTRACEMCO", "BAJFINANCE", "WIPRO", "HCLTECH",
    "NESTLEIND", "POWERGRID", "NTPC", "TECHM", "INDUSINDBK",
    "BAJAJFINSV", "ONGC", "COALINDIA", "ADANIENT", "ADANIPORTS",
    "DIVISLAB", "DRREDDY", "EICHERMOT", "GRASIM", "HEROMOTOCO",
    "HINDALCO", "JSWSTEEL", "M&M", "SBILIFE", "TATACONSUM",
    "TATASTEEL", "CIPLA", "APOLLOHOSP", "BAJAJ-AUTO", "BPCL",
    "BRITANNIA", "HDFCLIFE", "INDIGO", "ETERNAL", "TATAMOTORS",
)


def to_yfinance_ticker(symbol: str) -> str:
    """Convert an NSE root symbol to its yfinance ticker.

    >>> to_yfinance_ticker("RELIANCE")
    'RELIANCE.NS'
    >>> to_yfinance_ticker("BAJAJ-AUTO")
    'BAJAJ-AUTO.NS'
    >>> to_yfinance_ticker("BAJAJ_AUTO")     # accept fs-safe input too
    'BAJAJ-AUTO.NS'
    """
    return to_nse(symbol) + ".NS"


def to_groww_ticker(symbol: str) -> str:
    """Groww uses NSE-root form directly."""
    return to_nse(symbol)


def to_nse(symbol: str) -> str:
    """Normalise filesystem-safe form back to NSE root.

    >>> to_nse("BAJAJ_AUTO")
    'BAJAJ-AUTO'
    >>> to_nse("BAJAJ-AUTO")
    'BAJAJ-AUTO'
    """
    return symbol.upper().replace("_", "-")


def to_fs_safe(symbol: str) -> str:
    """Normalise NSE root to filesystem-safe form (used in stocks_1h/).

    >>> to_fs_safe("BAJAJ-AUTO")
    'BAJAJ_AUTO'
    >>> to_fs_safe("BAJAJ_AUTO")
    'BAJAJ_AUTO'
    """
    return symbol.upper().replace("-", "_")


def is_nifty_50(symbol: str) -> bool:
    """Membership check, normalisation-tolerant."""
    return to_nse(symbol) in NIFTY_50
