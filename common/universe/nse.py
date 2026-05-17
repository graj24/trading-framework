"""NSE full instrument universe — single source of truth for all PMs.

Loads from NSE bhavcopy CSV (cached daily) or falls back to the bundled
bse_scrip_master.csv. Provides filtering helpers so any PM can query
"all midcap IT stocks" without duplicating logic.
"""
from __future__ import annotations

import csv
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)

_CACHE_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "nse_universe.csv"
_BSE_FALLBACK = Path(__file__).resolve().parent.parent.parent / "data" / "bse_scrip_master.csv"

# Market-cap tiers (approximate, based on SEBI classification)
LARGE_CAP_SYMBOLS = frozenset([
    "RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK", "HINDUNILVR", "SBIN",
    "BHARTIARTL", "ITC", "KOTAKBANK", "LT", "AXISBANK", "ASIANPAINT", "MARUTI",
    "TITAN", "SUNPHARMA", "ULTRACEMCO", "BAJFINANCE", "WIPRO", "HCLTECH",
    "NESTLEIND", "POWERGRID", "NTPC", "TECHM", "INDUSINDBK", "BAJAJFINSV",
    "ONGC", "COALINDIA", "ADANIENT", "ADANIPORTS", "DIVISLAB", "DRREDDY",
    "EICHERMOT", "GRASIM", "HEROMOTOCO", "HINDALCO", "JSWSTEEL", "M&M",
    "SBILIFE", "TATACONSUM", "TATASTEEL", "CIPLA", "APOLLOHOSP", "BAJAJ-AUTO",
    "BPCL", "BRITANNIA", "HDFCLIFE", "INDIGO", "ETERNAL", "TATAMOTORS",
])


@dataclass
class Symbol:
    symbol: str
    name: str = ""
    isin: str = ""
    sector: str = ""
    market_cap_tier: str = "unknown"  # large / mid / small / sme / unknown
    lot_size: int = 1
    exchange: str = "NSE"

    def __hash__(self):
        return hash(self.symbol)

    def __eq__(self, other):
        return isinstance(other, Symbol) and self.symbol == other.symbol


_universe: list[Symbol] | None = None


def _load_from_bse_fallback() -> list[Symbol]:
    """Load symbols from bundled bse_scrip_master.csv."""
    if not _BSE_FALLBACK.exists():
        return []
    symbols = []
    try:
        with open(_BSE_FALLBACK, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                sym = (row.get("NSE Symbol") or row.get("symbol") or "").strip().upper()
                if not sym or sym == "0":
                    continue
                name = (row.get("Issuer Name") or row.get("name") or "").strip()
                isin = (row.get("ISIN No") or row.get("isin") or "").strip()
                tier = "large" if sym in LARGE_CAP_SYMBOLS else "unknown"
                symbols.append(Symbol(symbol=sym, name=name, isin=isin, market_cap_tier=tier))
    except Exception as e:
        logger.warning(f"BSE fallback load failed: {e}")
    return symbols


def _load_from_cache() -> list[Symbol]:
    """Load from cached nse_universe.csv if it exists."""
    if not _CACHE_PATH.exists():
        return []
    symbols = []
    try:
        with open(_CACHE_PATH, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                sym = row.get("symbol", "").strip().upper()
                if not sym:
                    continue
                symbols.append(Symbol(
                    symbol=sym,
                    name=row.get("name", ""),
                    isin=row.get("isin", ""),
                    sector=row.get("sector", ""),
                    market_cap_tier=row.get("market_cap_tier", "unknown"),
                    lot_size=int(row.get("lot_size", 1) or 1),
                    exchange=row.get("exchange", "NSE"),
                ))
    except Exception as e:
        logger.warning(f"NSE universe cache load failed: {e}")
    return symbols


def load_nse_universe(force_reload: bool = False) -> list[Symbol]:
    """Return the full NSE universe. Cached in memory after first load."""
    global _universe
    if _universe is not None and not force_reload:
        return _universe

    symbols = _load_from_cache()
    if not symbols:
        logger.info("NSE universe cache not found — falling back to BSE scrip master")
        symbols = _load_from_bse_fallback()

    # Deduplicate by symbol
    seen: set[str] = set()
    deduped = []
    for s in symbols:
        if s.symbol not in seen:
            seen.add(s.symbol)
            deduped.append(s)

    _universe = deduped
    logger.info(f"NSE universe loaded: {len(_universe)} symbols")
    return _universe


def find_symbols(
    tier: str | None = None,
    sector: str | None = None,
    exchange: str | None = None,
    min_lot: int | None = None,
    custom_filter: Callable[[Symbol], bool] | None = None,
) -> list[Symbol]:
    """Filter the universe. All filters are AND-combined."""
    universe = load_nse_universe()
    result = universe
    if tier:
        result = [s for s in result if s.market_cap_tier == tier]
    if sector:
        result = [s for s in result if sector.lower() in s.sector.lower()]
    if exchange:
        result = [s for s in result if s.exchange == exchange]
    if min_lot is not None:
        result = [s for s in result if s.lot_size >= min_lot]
    if custom_filter:
        result = [s for s in result if custom_filter(s)]
    return result


def get_symbol(sym: str) -> Symbol | None:
    """Look up a single symbol by name."""
    sym = sym.upper().strip()
    for s in load_nse_universe():
        if s.symbol == sym:
            return s
    return None


def update_nse_universe() -> int:
    """Download latest NSE bhavcopy and update the cache. Returns symbol count."""
    import requests
    import io

    url = "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "text/csv",
    }
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        reader = csv.DictReader(io.StringIO(resp.text))
        rows = list(reader)

        _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(_CACHE_PATH, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["symbol", "name", "isin", "sector", "market_cap_tier", "lot_size", "exchange"])
            writer.writeheader()
            for row in rows:
                sym = row.get("SYMBOL", "").strip().upper()
                if not sym:
                    continue
                tier = "large" if sym in LARGE_CAP_SYMBOLS else "unknown"
                writer.writerow({
                    "symbol": sym,
                    "name": row.get("NAME OF COMPANY", "").strip(),
                    "isin": row.get("ISIN NUMBER", "").strip(),
                    "sector": "",
                    "market_cap_tier": tier,
                    "lot_size": 1,
                    "exchange": "NSE",
                })

        global _universe
        _universe = None  # force reload
        loaded = load_nse_universe(force_reload=True)
        logger.info(f"NSE universe updated: {len(loaded)} symbols")
        return len(loaded)
    except Exception as e:
        logger.error(f"NSE universe update failed: {e}")
        return 0
