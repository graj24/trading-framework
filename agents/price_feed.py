"""
Price-feed daemon — the ONLY process that calls NSE.

Polls NSE for all watchlist symbols and writes to prices.db.
All other processes (strategist, api, scheduler) read from prices.db.

Poll cadence:
  - Market hours (09:15–15:30 IST weekdays): every 30s
  - Outside market hours: every 5 min (just to keep cache warm)
"""
from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

import common.pricing as pricing
from core.config import get_config

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("price-feed")

_MAX_WORKERS = 10  # concurrent NSE fetches per poll cycle


def _make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json",
        "Referer": "https://www.nseindia.com",
    })
    try:
        s.get("https://www.nseindia.com", timeout=8)
    except Exception:
        pass
    return s


def _fetch_one(session: requests.Session, sym: str) -> tuple[str, float, float] | None:
    """Fetch a single symbol from NSE. Returns (symbol, price, prev_close) or None."""
    try:
        r = session.get(
            f"https://www.nseindia.com/api/quote-equity?symbol={sym}",
            timeout=6,
        )
        if r.status_code != 200:
            return None
        pi = r.json().get("priceInfo", {})
        price = pi.get("lastPrice") or pi.get("close")
        prev = pi.get("previousClose") or price
        if price:
            return sym, float(price), float(prev or price)
    except Exception as e:
        logger.debug(f"fetch {sym}: {e}")
    return None


def poll_once(session: requests.Session, symbols: list[str]) -> int:
    """Fetch all symbols concurrently, write to cache. Returns count updated."""
    updated = 0
    with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as ex:
        futures = {ex.submit(_fetch_one, session, sym): sym for sym in symbols}
        for fut in as_completed(futures):
            result = fut.result()
            if result:
                sym, price, prev = result
                pricing.upsert(sym, price, prev)
                updated += 1
    return updated


def run():
    logger.info("Price-feed daemon starting")
    session = _make_session()
    consecutive_errors = 0
    poll_count = 0

    while True:
        try:
            config = get_config()
            symbols = config.get("watchlist", [])[:50]
            if not symbols:
                time.sleep(60)
                continue

            updated = poll_once(session, symbols)
            market_open = pricing.is_market_open()
            interval = pricing.poll_interval_seconds()
            poll_count += 1

            log_msg = (
                f"Poll #{poll_count}: {updated}/{len(symbols)} symbols "
                f"({'market open' if market_open else 'market closed'}) "
                f"— next in {interval}s"
            )
            # Log DB stats every 10 polls
            if poll_count % 10 == 0:
                stats = pricing.get_db_stats()
                log_msg += f" | DB: {stats}"

            logger.info(log_msg)
            consecutive_errors = 0
            time.sleep(interval)

        except Exception as e:
            consecutive_errors += 1
            wait = min(60 * consecutive_errors, 300)
            logger.warning(f"Poll error (#{consecutive_errors}): {e} — retrying in {wait}s")
            if consecutive_errors % 3 == 0:
                logger.info("Re-seeding NSE session")
                session = _make_session()
            time.sleep(wait)


if __name__ == "__main__":
    run()
