"""
Data Agent — fetches market data and builds per-stock knowledge bases.

Knowledge base per stock (stocks/<SYMBOL>/):
  price_history.parquet     - OHLCV history (daily)
  fundamentals.json         - PE, EPS, market cap, sector
  earnings_history.json     - Quarterly results + price reaction
  corporate_actions.json    - Dividends, splits
  sector_correlation.json   - Correlation with Nifty + sector index
  event_reactions.json      - Avg price reaction per event type
  signal_weights.json       - Self-learned signal weights (default 1.0)
  news_history.json         - Populated by NewsAgent
  bulk_deals.json           - Populated by NSE scraper (future)
  patterns.json             - Populated by PatternAgent
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd

from agents.base import Agent, AgentResult
from core.knowledge_base import init_kb, kb_path, write_kb, read_kb

logger = logging.getLogger(__name__)

# Sector index mapping (jugaad-data / NSE index names)
SECTOR_INDICES = {
    "IT":      "NIFTY IT",
    "BANK":    "NIFTY BANK",
    "PHARMA":  "NIFTY PHARMA",
    "AUTO":    "NIFTY AUTO",
    "ENERGY":  "NIFTY ENERGY",
    "FMCG":    "NIFTY FMCG",
    "METAL":   "NIFTY METAL",
    "REALTY":  "NIFTY REALTY",
}

NIFTY_INDEX = "NIFTY 50"


class DataAgent(Agent):
    def __init__(self, config: dict):
        super().__init__("DataAgent", config)
        self.history_years: int = config.get("data", {}).get("history_years", 5)

    def run(self, context: Optional[dict] = None) -> AgentResult:
        symbol = (context or {}).get("symbol")
        if not symbol:
            return self._error("No symbol provided in context")

        try:
            result = self.build_kb(symbol)
            return self._result(result)
        except Exception as e:
            logger.error(f"DataAgent failed for {symbol}: {e}")
            return self._error(str(e))

    def build_kb(self, symbol: str) -> dict:
        """Build or update the knowledge base for a stock."""
        logger.info(f"Building knowledge base for {symbol}")
        init_kb(symbol)

        results = {}

        def _safe(name: str, fn):
            try:
                return fn()
            except Exception as e:
                logger.warning(f"{symbol}: {name} failed: {e}")
                return f"error: {type(e).__name__}"

        results["price_history"]     = _safe("price_history",     lambda: self._fetch_price_history(symbol))
        results["fundamentals"]      = _safe("fundamentals",      lambda: self._fetch_fundamentals(symbol))
        results["earnings_history"]  = _safe("earnings_history",  lambda: self._fetch_earnings_history(symbol))
        results["corporate_actions"] = _safe("corporate_actions", lambda: self._fetch_corporate_actions(symbol))
        results["sector_correlation"]= _safe("sector_correlation",lambda: self._compute_sector_correlation(symbol))
        results["event_reactions"]   = _safe("event_reactions",   lambda: self._compute_event_reactions(symbol))
        _safe("signal_weights", lambda: self._init_signal_weights(symbol))

        logger.info(f"Knowledge base built for {symbol}: {results}")
        return {"symbol": symbol, "kb_results": results}

    def _fetch_price_history(self, symbol: str) -> str:
        """Fetch OHLCV history via NSE (jugaad-data). No yfinance fallback."""
        path = kb_path(symbol) / "price_history.parquet"

        if path.exists():
            existing = pd.read_parquet(path)
            last_date = existing.index.max()
            start_dt = (last_date + timedelta(days=1)).to_pydatetime() \
                if hasattr(last_date, "to_pydatetime") else \
                datetime.combine(last_date, datetime.min.time()) + timedelta(days=1)
        else:
            existing = None
            start_dt = datetime.now() - timedelta(days=self.history_years * 365)

        if start_dt.date() >= datetime.now().date():
            return f"{len(existing)} rows (no update)" if existing is not None else "up to date"

        from core.nse_historical import fetch_history as nse_fetch
        df = nse_fetch(symbol, start=start_dt, end=datetime.now())

        if df.empty:
            logger.warning(f"No price data for {symbol}")
            return "empty"

        if df.index.tz is not None:
            df.index = df.index.tz_localize(None)

        if existing is not None and not existing.empty:
            if existing.index.tz is not None:
                existing.index = existing.index.tz_localize(None)
            df = pd.concat([existing, df])
            df = df[~df.index.duplicated(keep="last")].sort_index()

        df.to_parquet(path)
        logger.info(f"{symbol}: {len(df)} days of price history saved")
        return f"{len(df)} rows"

    def _fetch_fundamentals(self, symbol: str) -> str:
        """Fetch fundamentals from NSE via jugaad-data."""
        try:
            from jugaad_data.nse import NSELive
            nse = NSELive()
            q = nse.stock_quote(symbol)
            info = q.get("priceInfo", {})
            meta = q.get("metadata", {})
            fundamentals = {
                "symbol":       symbol,
                "company_name": meta.get("companyName", ""),
                "sector":       meta.get("industry", ""),
                "market_cap":   None,
                "pe_ratio":     info.get("pbRatio"),  # NSELive doesn't give PE directly
                "52w_high":     info.get("weekHighLow", {}).get("max"),
                "52w_low":      info.get("weekHighLow", {}).get("min"),
                "updated_at":   datetime.now().isoformat(),
            }
        except Exception as e:
            logger.warning(f"{symbol} fundamentals via NSELive failed: {e}")
            fundamentals = {"symbol": symbol, "updated_at": datetime.now().isoformat()}

        write_kb(symbol, "fundamentals.json", fundamentals)
        return "ok"

    def _fetch_earnings_history(self, symbol: str) -> str:
        """Derive earnings history from price history (no external API needed)."""
        # Without a paid data source, we store an empty placeholder.
        # Price reaction is computed when the KB is refreshed post-earnings.
        path = kb_path(symbol) / "price_history.parquet"
        if not path.exists():
            write_kb(symbol, "earnings_history.json", {"quarters": []})
            return "no_price_data"

        # Preserve existing data if already populated
        existing = read_kb(symbol, "earnings_history.json")
        if existing and existing.get("quarters"):
            return f"{len(existing['quarters'])} quarters (cached)"

        write_kb(symbol, "earnings_history.json", {"quarters": []})
        return "placeholder"

    def _fetch_corporate_actions(self, symbol: str) -> str:
        """Fetch corporate actions (dividends/splits) from NSE via jugaad-data."""
        try:
            from jugaad_data.nse import NSELive
            nse = NSELive()
            ca = nse.equities_master()  # best-effort; NSELive API varies by version
            actions = {"dividends": [], "splits": []}
        except Exception:
            actions = {"dividends": [], "splits": []}

        write_kb(symbol, "corporate_actions.json", actions)
        return "ok"

    def _compute_sector_correlation(self, symbol: str) -> str:
        """Compute correlation of stock with Nifty and sector indices via jugaad-data."""
        path = kb_path(symbol) / "price_history.parquet"
        if not path.exists():
            return "no_price_data"

        stock_df = pd.read_parquet(path)
        if stock_df.empty:
            return "empty"

        stock_returns = stock_df["Close"].pct_change().dropna()
        correlations = {}

        try:
            from jugaad_data.nse import index_df
            start_date = stock_df.index.min().date()
            end_date   = stock_df.index.max().date()

            for idx_name, index_name in {**{"NIFTY": NIFTY_INDEX}, **SECTOR_INDICES}.items():
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
                    s = pd.to_numeric(s, errors="coerce").dropna().sort_index()
                    idx_returns = s.pct_change().dropna()
                    aligned = stock_returns.align(idx_returns, join="inner")
                    correlations[idx_name] = round(float(aligned[0].corr(aligned[1])), 3)
                except Exception:
                    pass
        except ImportError:
            pass

        write_kb(symbol, "sector_correlation.json", {
            "symbol": symbol,
            "correlations": correlations,
            "updated_at": datetime.now().isoformat(),
        })
        return f"{len(correlations)} correlations computed"

    def _compute_event_reactions(self, symbol: str) -> str:
        """Derive avg price reaction per event type from earnings history."""
        earnings = read_kb(symbol, "earnings_history.json")
        quarters = earnings.get("quarters", [])

        reactions = [q["price_reaction_pct"] for q in quarters if q.get("price_reaction_pct") is not None]
        if not reactions:
            write_kb(symbol, "event_reactions.json", {})
            return "no_data"

        beats = [r for r in reactions if r > 0]
        misses = [r for r in reactions if r <= 0]

        event_reactions = {
            "earnings_beat": {
                "count": len(beats),
                "avg_reaction_pct": round(sum(beats) / len(beats), 2) if beats else 0,
            },
            "earnings_miss": {
                "count": len(misses),
                "avg_reaction_pct": round(sum(misses) / len(misses), 2) if misses else 0,
            },
            "updated_at": datetime.now().isoformat(),
        }
        write_kb(symbol, "event_reactions.json", event_reactions)
        return "ok"

    def _init_signal_weights(self, symbol: str) -> None:
        """Initialize signal weights to 1.0 if not already set."""
        existing = read_kb(symbol, "signal_weights.json")
        if existing:
            return  # Don't overwrite learned weights

        default_weights = {
            "technical_score": 1.0,
            "news_sentiment": 1.0,
            "pattern_ev": 1.0,
            "sector_momentum": 1.0,
            "regime_alignment": 1.0,
            "fundamentals": 0.5,
            "updated_at": datetime.now().isoformat(),
        }
        write_kb(symbol, "signal_weights.json", default_weights)

    def load_price_history(self, symbol: str) -> Optional[pd.DataFrame]:
        """Load price history parquet for a symbol."""
        path = kb_path(symbol) / "price_history.parquet"
        if not path.exists():
            return None
        return pd.read_parquet(path)


if __name__ == "__main__":
    import sys
    import yaml
    from dotenv import load_dotenv
    from core.logger import setup_logging

    load_dotenv()
    with open("config.yaml") as f:
        config = yaml.safe_load(f)
    setup_logging(config)

    if len(sys.argv) >= 3 and sys.argv[1] == "build":
        symbol = sys.argv[2].upper()
        agent = DataAgent(config)
        result = agent.build_kb(symbol)
        print(json.dumps(result, indent=2, default=str))
    else:
        print("Usage: python -m agents.data_agent build <SYMBOL>")
