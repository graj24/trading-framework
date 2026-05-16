"""SectorRotationAgent — relative-strength signal for sector rotation.

P3: computes 1-month and 3-month returns for NSE sector indices using
yfinance, ranks them, and returns a signal that MasterAgent can inject
into the LLM prompt.

No paid data feed required — uses the same yfinance sector tickers already
used by ml_model.py.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd
import yfinance as yf

from agents.base import Agent, AgentResult

logger = logging.getLogger(__name__)

# NSE sector indices available on Yahoo Finance
SECTOR_TICKERS: dict[str, str] = {
    "nifty":      "^NSEI",
    "banknifty":  "^NSEBANK",
    "it":         "^CNXIT",
    "fmcg":       "^CNXFMCG",
    "auto":       "^CNXAUTO",
    "energy":     "^CNXENERGY",
    "pharma":     "^CNXPHARMA",
    "metal":      "^CNXMETAL",
    "realty":     "^CNXREALTY",
    "infra":      "^CNXINFRA",
}

# Map stock sectors (from yfinance info) to the closest index
SECTOR_MAP: dict[str, str] = {
    "Technology":           "it",
    "Consumer Defensive":   "fmcg",
    "Consumer Cyclical":    "auto",
    "Energy":               "energy",
    "Healthcare":           "pharma",
    "Basic Materials":      "metal",
    "Real Estate":          "realty",
    "Industrials":          "infra",
    "Financial Services":   "banknifty",
    "Communication Services": "it",
}


def _fetch_sector_returns(lookback_days: int = 90) -> dict[str, dict]:
    """Fetch sector index returns for the past *lookback_days* days.

    Returns {sector_name: {ret_1m, ret_3m, rank_1m, rank_3m, ticker}}
    """
    end   = datetime.now()
    start = end - timedelta(days=lookback_days + 10)  # buffer for weekends

    results: dict[str, dict] = {}
    for name, ticker in SECTOR_TICKERS.items():
        try:
            df = yf.Ticker(ticker).history(
                start=start.strftime("%Y-%m-%d"),
                end=end.strftime("%Y-%m-%d"),
                interval="1d",
                progress=False,
            )
            if df.empty or len(df) < 5:
                continue
            close = df["Close"].dropna()
            ret_1m = float((close.iloc[-1] / close.iloc[max(-22, -len(close))] - 1) * 100)
            ret_3m = float((close.iloc[-1] / close.iloc[max(-66, -len(close))] - 1) * 100)
            results[name] = {
                "ticker":  ticker,
                "ret_1m":  round(ret_1m, 2),
                "ret_3m":  round(ret_3m, 2),
                "latest":  round(float(close.iloc[-1]), 2),
            }
        except Exception as e:
            logger.debug("sector fetch failed for %s: %s", name, e)

    if not results:
        return results

    # Rank sectors (1 = strongest)
    by_1m = sorted(results, key=lambda k: results[k]["ret_1m"], reverse=True)
    by_3m = sorted(results, key=lambda k: results[k]["ret_3m"], reverse=True)
    for i, name in enumerate(by_1m):
        results[name]["rank_1m"] = i + 1
    for i, name in enumerate(by_3m):
        results[name]["rank_3m"] = i + 1

    return results


def sector_signal_for_stock(symbol: str, sector_returns: dict[str, dict]) -> dict:
    """Return a sector-relative-strength signal for a single stock.

    Looks up the stock's sector via yfinance, maps it to the closest NSE
    sector index, and returns whether the sector is in the top/bottom half.
    """
    sector_name = "unknown"
    mapped_index = None
    try:
        info = yf.Ticker(symbol + ".NS").info or {}
        yf_sector = info.get("sector", "")
        mapped_index = SECTOR_MAP.get(yf_sector)
        sector_name = yf_sector
    except Exception:
        pass

    if not mapped_index or mapped_index not in sector_returns:
        return {
            "symbol":        symbol,
            "sector":        sector_name,
            "sector_index":  mapped_index,
            "rank_1m":       None,
            "rank_3m":       None,
            "ret_1m":        None,
            "ret_3m":        None,
            "signal":        "NEUTRAL",
            "note":          "sector data unavailable",
        }

    data    = sector_returns[mapped_index]
    n       = len(sector_returns)
    rank_1m = data["rank_1m"]
    rank_3m = data["rank_3m"]

    # Top third = STRONG, middle = NEUTRAL, bottom third = WEAK
    if rank_1m <= n // 3 and rank_3m <= n // 3:
        signal = "STRONG"
    elif rank_1m > (2 * n // 3) and rank_3m > (2 * n // 3):
        signal = "WEAK"
    else:
        signal = "NEUTRAL"

    return {
        "symbol":       symbol,
        "sector":       sector_name,
        "sector_index": mapped_index,
        "rank_1m":      rank_1m,
        "rank_3m":      rank_3m,
        "ret_1m":       data["ret_1m"],
        "ret_3m":       data["ret_3m"],
        "signal":       signal,
        "note":         f"{mapped_index} rank {rank_1m}/{n} (1m), {rank_3m}/{n} (3m)",
    }


class SectorRotationAgent(Agent):
    """Computes sector relative-strength and injects it into the LLM prompt."""

    def __init__(self, config: dict):
        super().__init__("SectorRotationAgent", config)
        self._cache: dict[str, dict] | None = None
        self._cache_ts: datetime | None = None
        self._cache_ttl_minutes = 60

    def _get_sector_returns(self) -> dict[str, dict]:
        """Return cached sector returns, refreshing if stale."""
        now = datetime.now()
        if (
            self._cache is None
            or self._cache_ts is None
            or (now - self._cache_ts).seconds > self._cache_ttl_minutes * 60
        ):
            self._cache    = _fetch_sector_returns()
            self._cache_ts = now
        return self._cache

    def run(self, context: Optional[dict] = None) -> AgentResult:
        """Return sector returns for all tracked indices."""
        try:
            returns = self._get_sector_returns()
            return self._result({
                "sector_returns": returns,
                "top_sectors":    [k for k, v in returns.items() if v.get("rank_1m", 99) <= 3],
                "weak_sectors":   [k for k, v in returns.items() if v.get("rank_1m", 0) > len(returns) - 3],
                "fetched_at":     datetime.now().isoformat(),
            })
        except Exception as e:
            logger.exception("SectorRotationAgent failed")
            return self._error(str(e))

    def signal_for_stock(self, symbol: str) -> dict:
        """Convenience wrapper — returns sector signal for a single stock."""
        returns = self._get_sector_returns()
        return sector_signal_for_stock(symbol, returns)
