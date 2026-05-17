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
import yfinance as yf

from agents.base import Agent, AgentResult
from core.knowledge_base import init_kb, kb_path, write_kb, read_kb

logger = logging.getLogger(__name__)

# NSE suffix for yfinance
NSE_SUFFIX = ".NS"

# Sector index mapping (yfinance tickers)
SECTOR_INDICES = {
    "IT": "^CNXIT",
    "BANK": "^NSEBANK",
    "PHARMA": "^CNXPHARMA",
    "AUTO": "^CNXAUTO",
    "ENERGY": "^CNXENERGY",
    "FMCG": "^CNXFMCG",
    "METAL": "^CNXMETAL",
    "REALTY": "^CNXREALTY",
}

NIFTY_TICKER = "^NSEI"


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

        ticker = symbol.upper() + NSE_SUFFIX
        yf_ticker = yf.Ticker(ticker)

        results = {}

        # 1. Price history
        results["price_history"] = self._fetch_price_history(symbol, yf_ticker)

        # 2. Fundamentals
        results["fundamentals"] = self._fetch_fundamentals(symbol, yf_ticker)

        # 3. Earnings history + price reaction
        results["earnings_history"] = self._fetch_earnings_history(symbol, yf_ticker)

        # 4. Corporate actions (dividends, splits)
        results["corporate_actions"] = self._fetch_corporate_actions(symbol, yf_ticker)

        # 5. Sector correlation
        results["sector_correlation"] = self._compute_sector_correlation(symbol)

        # 6. Event reactions (derived from earnings history)
        results["event_reactions"] = self._compute_event_reactions(symbol)

        # 7. Default signal weights
        self._init_signal_weights(symbol)

        logger.info(f"Knowledge base built for {symbol}: {results}")
        return {"symbol": symbol, "kb_results": results}

    def _fetch_price_history(self, symbol: str, ticker: yf.Ticker) -> str:
        """Fetch OHLCV history and save as parquet."""
        path = kb_path(symbol) / "price_history.parquet"
        start = (datetime.now() - timedelta(days=self.history_years * 365)).strftime("%Y-%m-%d")

        # Incremental: only fetch new data if file exists
        if path.exists():
            existing = pd.read_parquet(path)
            last_date = existing.index.max()
            start = (last_date + timedelta(days=1)).strftime("%Y-%m-%d")
            df = ticker.history(start=start, interval="1d")
            if not df.empty:
                df = pd.concat([existing, df])
                df = df[~df.index.duplicated(keep="last")]
        else:
            df = ticker.history(start=start, interval="1d")

        if df.empty:
            logger.warning(f"No price data for {symbol}")
            return "empty"

        df.to_parquet(path)
        logger.info(f"{symbol}: {len(df)} days of price history saved")
        return f"{len(df)} rows"

    def _fetch_fundamentals(self, symbol: str, ticker: yf.Ticker) -> str:
        """Fetch key fundamentals from yfinance."""
        info = ticker.info or {}
        fundamentals = {
            "symbol": symbol,
            "company_name": info.get("longName", ""),
            "sector": info.get("sector", ""),
            "industry": info.get("industry", ""),
            "market_cap": info.get("marketCap"),
            "pe_ratio": info.get("trailingPE"),
            "forward_pe": info.get("forwardPE"),
            "eps": info.get("trailingEps"),
            "book_value": info.get("bookValue"),
            "price_to_book": info.get("priceToBook"),
            "debt_to_equity": info.get("debtToEquity"),
            "roe": info.get("returnOnEquity"),
            "revenue_growth": info.get("revenueGrowth"),
            "earnings_growth": info.get("earningsGrowth"),
            "dividend_yield": info.get("dividendYield"),
            "52w_high": info.get("fiftyTwoWeekHigh"),
            "52w_low": info.get("fiftyTwoWeekLow"),
            "avg_volume": info.get("averageVolume"),
            "updated_at": datetime.now().isoformat(),
        }
        write_kb(symbol, "fundamentals.json", fundamentals)
        return "ok"

    def _fetch_earnings_history(self, symbol: str, ticker: yf.Ticker) -> str:
        """Fetch quarterly earnings and compute price reaction."""
        try:
            earnings = ticker.quarterly_financials
            if earnings is None or earnings.empty:
                return "no_data"

            price_df = None
            path = kb_path(symbol) / "price_history.parquet"
            if path.exists():
                price_df = pd.read_parquet(path)

            history = []
            for col in earnings.columns:
                date_str = str(col.date()) if hasattr(col, "date") else str(col)
                entry = {"date": date_str, "revenue": None, "net_income": None, "price_reaction_pct": None}

                if "Total Revenue" in earnings.index:
                    entry["revenue"] = earnings.loc["Total Revenue", col]
                if "Net Income" in earnings.index:
                    entry["net_income"] = earnings.loc["Net Income", col]

                # Price reaction: close on earnings date vs previous close
                if price_df is not None and not price_df.empty:
                    try:
                        date = pd.Timestamp(date_str)
                        # Find nearest trading day
                        idx = price_df.index.searchsorted(date)
                        if 0 < idx < len(price_df):
                            reaction = (price_df.iloc[idx]["Close"] - price_df.iloc[idx - 1]["Close"]) / price_df.iloc[idx - 1]["Close"] * 100
                            entry["price_reaction_pct"] = round(reaction, 2)
                    except Exception:
                        pass

                history.append(entry)

            write_kb(symbol, "earnings_history.json", {"quarters": history})
            return f"{len(history)} quarters"
        except Exception as e:
            logger.warning(f"{symbol} earnings fetch failed: {e}")
            return "error"

    def _fetch_corporate_actions(self, symbol: str, ticker: yf.Ticker) -> str:
        """Fetch dividends and stock splits."""
        try:
            dividends = ticker.dividends
            splits = ticker.splits

            actions = {
                "dividends": [
                    {"date": str(d.date()), "amount": float(v)}
                    for d, v in dividends.items()
                ] if dividends is not None and not dividends.empty else [],
                "splits": [
                    {"date": str(d.date()), "ratio": float(v)}
                    for d, v in splits.items()
                ] if splits is not None and not splits.empty else [],
            }
            write_kb(symbol, "corporate_actions.json", actions)
            return f"{len(actions['dividends'])} dividends, {len(actions['splits'])} splits"
        except Exception as e:
            logger.warning(f"{symbol} corporate actions failed: {e}")
            return "error"

    def _compute_sector_correlation(self, symbol: str) -> str:
        """Compute correlation of stock with Nifty and sector indices."""
        path = kb_path(symbol) / "price_history.parquet"
        if not path.exists():
            return "no_price_data"

        stock_df = pd.read_parquet(path)
        if stock_df.empty:
            return "empty"

        stock_returns = stock_df["Close"].pct_change().dropna()
        correlations = {}

        # Nifty correlation
        try:
            nifty = yf.Ticker(NIFTY_TICKER).history(
                start=stock_df.index.min().strftime("%Y-%m-%d"), interval="1d"
            )
            if not nifty.empty:
                nifty_returns = nifty["Close"].pct_change().dropna()
                aligned = stock_returns.align(nifty_returns, join="inner")
                correlations["NIFTY"] = round(float(aligned[0].corr(aligned[1])), 3)
        except Exception as e:
            logger.warning(f"Nifty correlation failed for {symbol}: {e}")

        # Sector index correlations
        for sector_name, sector_ticker in SECTOR_INDICES.items():
            try:
                sector_df = yf.Ticker(sector_ticker).history(
                    start=stock_df.index.min().strftime("%Y-%m-%d"), interval="1d"
                )
                if not sector_df.empty:
                    sector_returns = sector_df["Close"].pct_change().dropna()
                    aligned = stock_returns.align(sector_returns, join="inner")
                    correlations[sector_name] = round(float(aligned[0].corr(aligned[1])), 3)
            except Exception:
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
