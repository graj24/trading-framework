"""SectorRotationAgent — relative-strength signal for sector rotation.

Uses jugaad-data NSE index historical data (works on EC2).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd

from agents.base import Agent, AgentResult

logger = logging.getLogger(__name__)

# NSE sector indices (jugaad-data index names)
SECTOR_TICKERS: dict[str, str] = {
    "nifty":     "NIFTY 50",
    "banknifty": "NIFTY BANK",
    "it":        "NIFTY IT",
    "fmcg":      "NIFTY FMCG",
    "auto":      "NIFTY AUTO",
    "energy":    "NIFTY ENERGY",
    "pharma":    "NIFTY PHARMA",
    "metal":     "NIFTY METAL",
    "realty":    "NIFTY REALTY",
    "infra":     "NIFTY INFRA",
}

# Map NSELive industry strings to sector index keys
SECTOR_MAP: dict[str, str] = {
    "Information Technology":   "it",
    "FMCG":                     "fmcg",
    "Automobile":               "auto",
    "Energy":                   "energy",
    "Pharma":                   "pharma",
    "Metal":                    "metal",
    "Realty":                   "realty",
    "Infrastructure":           "infra",
    "Financial Services":       "banknifty",
    "Banks":                    "banknifty",
    "Telecom":                  "it",
}


def _fetch_sector_returns(lookback_days: int = 90) -> dict[str, dict]:
    end_date   = datetime.now().date()
    start_date = (datetime.now() - timedelta(days=lookback_days + 10)).date()

    results: dict[str, dict] = {}
    try:
        from jugaad_data.nse import index_df
    except ImportError:
        logger.warning("jugaad-data not available; sector returns unavailable")
        return results

    for name, index_name in SECTOR_TICKERS.items():
        try:
            raw = index_df(index_name, from_date=start_date, to_date=end_date)
            if raw is None or raw.empty:
                continue
            close_col = next((c for c in raw.columns if "clos" in c.lower()), None)
            date_col  = next((c for c in raw.columns if "date" in c.lower()), None)
            if not close_col or not date_col:
                continue
            s = raw.set_index(date_col)[close_col]
            s.index = pd.to_datetime(s.index, errors="coerce").normalize()
            close = pd.to_numeric(s, errors="coerce").dropna().sort_index()
            if len(close) < 5:
                continue
            ret_1m = float((close.iloc[-1] / close.iloc[max(-22, -len(close))] - 1) * 100)
            ret_3m = float((close.iloc[-1] / close.iloc[max(-66, -len(close))] - 1) * 100)
            results[name] = {
                "index_name": index_name,
                "ret_1m":  round(ret_1m, 2),
                "ret_3m":  round(ret_3m, 2),
                "latest":  round(float(close.iloc[-1]), 2),
            }
        except Exception as e:
            logger.debug("sector fetch failed for %s: %s", name, e)

    if not results:
        return results

    by_1m = sorted(results, key=lambda k: results[k]["ret_1m"], reverse=True)
    by_3m = sorted(results, key=lambda k: results[k]["ret_3m"], reverse=True)
    for i, name in enumerate(by_1m):
        results[name]["rank_1m"] = i + 1
    for i, name in enumerate(by_3m):
        results[name]["rank_3m"] = i + 1

    return results


def _get_stock_sector(symbol: str) -> Optional[str]:
    """Look up stock sector via NSELive."""
    try:
        from jugaad_data.nse import NSELive
        nse = NSELive()
        q = nse.stock_quote(symbol)
        industry = q.get("metadata", {}).get("industry", "")
        return SECTOR_MAP.get(industry)
    except Exception:
        return None


def sector_signal_for_stock(symbol: str, sector_returns: dict[str, dict]) -> dict:
    mapped_index = _get_stock_sector(symbol)

    if not mapped_index or mapped_index not in sector_returns:
        return {
            "symbol":       symbol,
            "sector_index": mapped_index,
            "signal":       "NEUTRAL",
            "note":         "sector data unavailable",
        }

    data    = sector_returns[mapped_index]
    n       = len(sector_returns)
    rank_1m = data["rank_1m"]
    rank_3m = data["rank_3m"]

    if rank_1m <= n // 3 and rank_3m <= n // 3:
        signal = "STRONG"
    elif rank_1m > (2 * n // 3) and rank_3m > (2 * n // 3):
        signal = "WEAK"
    else:
        signal = "NEUTRAL"

    return {
        "symbol":       symbol,
        "sector_index": mapped_index,
        "rank_1m":      rank_1m,
        "rank_3m":      rank_3m,
        "ret_1m":       data["ret_1m"],
        "ret_3m":       data["ret_3m"],
        "signal":       signal,
        "note":         f"{mapped_index} rank {rank_1m}/{n} (1m), {rank_3m}/{n} (3m)",
    }


class SectorRotationAgent(Agent):
    def __init__(self, config: dict):
        super().__init__("SectorRotationAgent", config)
        self._cache: dict[str, dict] | None = None
        self._cache_ts: datetime | None = None
        self._cache_ttl_minutes = 60

    def _get_sector_returns(self) -> dict[str, dict]:
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
        returns = self._get_sector_returns()
        return sector_signal_for_stock(symbol, returns)
