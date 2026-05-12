"""
Stock Discovery Agent — finds trading opportunities from multiple sources:

1. NSE Top Gainers/Losers (official NSE API)
2. Unusual Volume (3× average — institutional activity)
3. NSE Bulk Deals (FII/DII buying)
4. MoneyControl Most Active / Trending
5. Twitter/X sentiment via nitter scraping (free, no API needed)
6. Google Trends India (search spike = retail interest)

Discovered stocks are scored, ranked, and added to the watchlist.
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timedelta
from typing import Optional

import requests
import yaml
from bs4 import BeautifulSoup

from agents.base import Agent, AgentResult

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}
TIMEOUT = 10

# Nitter instances (public, no auth needed)
NITTER_INSTANCES = [
    "https://nitter.privacydev.net",
    "https://nitter.poast.org",
    "https://nitter.1d4.us",
]

POSITIVE_KW = {"buy", "bullish", "breakout", "surge", "rally", "strong", "beat",
               "upgrade", "target", "upside", "long", "accumulate", "positive"}
NEGATIVE_KW = {"sell", "bearish", "crash", "dump", "weak", "miss", "downgrade",
               "short", "avoid", "negative", "fall", "drop", "fraud"}


# ── Source 1: NSE Top Gainers / Losers ───────────────────────────────────────

def fetch_nse_movers() -> list[dict]:
    """Fetch top gainers and losers from NSE."""
    results = []
    session = requests.Session()
    try:
        session.get("https://www.nseindia.com", headers=HEADERS, timeout=TIMEOUT)
        for category in ["gainers", "losers"]:
            url = f"https://www.nseindia.com/api/live-analysis-variations?index=gainers" \
                  if category == "gainers" else \
                  "https://www.nseindia.com/api/live-analysis-variations?index=loosers"
            resp = session.get(url, headers=HEADERS, timeout=TIMEOUT)
            data = resp.json()
            for item in (data.get("NIFTY", {}).get("data", []) or [])[:10]:
                symbol = item.get("symbol", "")
                change_pct = item.get("perChange", 0)
                if symbol:
                    results.append({
                        "symbol": symbol,
                        "source": f"nse_{category}",
                        "change_pct": change_pct,
                        "score": abs(change_pct) * 0.5,
                        "reason": f"NSE {category}: {change_pct:+.1f}%",
                    })
    except Exception as e:
        logger.debug(f"NSE movers failed: {e}")
    return results


# ── Source 2: Unusual Volume ──────────────────────────────────────────────────

def fetch_unusual_volume() -> list[dict]:
    """Find stocks trading at 3× their average volume."""
    results = []
    try:
        session = requests.Session()
        session.get("https://www.nseindia.com", headers=HEADERS, timeout=TIMEOUT)
        url = "https://www.nseindia.com/api/live-analysis-volume-gainers"
        resp = session.get(url, headers=HEADERS, timeout=TIMEOUT)
        data = resp.json()
        for item in (data.get("data", []) or [])[:15]:
            symbol = item.get("symbol", "")
            vol_ratio = item.get("quantityTraded", 0) / max(item.get("averageQuantityTraded", 1), 1)
            if symbol and vol_ratio >= 2.0:
                results.append({
                    "symbol": symbol,
                    "source": "unusual_volume",
                    "vol_ratio": round(vol_ratio, 1),
                    "score": min(vol_ratio * 0.8, 5.0),
                    "reason": f"Volume {vol_ratio:.1f}× average",
                })
    except Exception as e:
        logger.debug(f"Unusual volume failed: {e}")
    return results


# ── Source 3: NSE Bulk Deals ──────────────────────────────────────────────────

def fetch_bulk_deals() -> list[dict]:
    """Fetch today's bulk deals — FII/DII buying is a strong signal."""
    results = []
    try:
        session = requests.Session()
        session.get("https://www.nseindia.com", headers=HEADERS, timeout=TIMEOUT)
        url = "https://www.nseindia.com/api/bulk-deals"
        resp = session.get(url, headers=HEADERS, timeout=TIMEOUT)
        data = resp.json()
        for item in (data.get("data", []) or [])[:20]:
            symbol = item.get("symbol", "")
            buy_sell = item.get("buySell", "")
            client = item.get("clientName", "")
            qty = item.get("quantityTraded", 0)
            if symbol and buy_sell == "BUY":
                # FII/DII/mutual fund buying = strong signal
                is_institutional = any(kw in client.upper() for kw in
                                       ["FII", "FPI", "MUTUAL", "FUND", "LTD", "INSURANCE", "TRUST"])
                score = 3.0 if is_institutional else 1.5
                results.append({
                    "symbol": symbol,
                    "source": "bulk_deal",
                    "client": client,
                    "qty": qty,
                    "score": score,
                    "reason": f"Bulk BUY by {client[:30]}",
                })
    except Exception as e:
        logger.debug(f"Bulk deals failed: {e}")
    return results


# ── Source 4: MoneyControl Most Active ───────────────────────────────────────

def fetch_moneycontrol_active() -> list[dict]:
    """Scrape MoneyControl most active stocks."""
    results = []
    try:
        url = "https://www.moneycontrol.com/stocks/marketstats/nsegainer/index.php"
        resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        soup = BeautifulSoup(resp.text, "html.parser")
        for row in soup.select("table.bsr_table tr")[1:11]:
            cols = row.find_all("td")
            if len(cols) >= 2:
                name = cols[0].get_text(strip=True)
                change = cols[-1].get_text(strip=True).replace("%", "")
                # Extract NSE symbol from link if available
                a = cols[0].find("a")
                href = a.get("href", "") if a else ""
                # MoneyControl URLs contain the symbol
                symbol = href.split("/")[-1].split("-")[0].upper() if href else ""
                if symbol and len(symbol) <= 15:
                    try:
                        chg = float(change)
                        results.append({
                            "symbol": symbol,
                            "source": "moneycontrol_active",
                            "score": abs(chg) * 0.3,
                            "reason": f"MoneyControl active: {chg:+.1f}%",
                        })
                    except ValueError:
                        pass
    except Exception as e:
        logger.debug(f"MoneyControl active failed: {e}")
    return results


# ── Source 5: Twitter/X via Nitter ───────────────────────────────────────────

def fetch_twitter_sentiment(symbol: str) -> dict:
    """Scrape Twitter mentions via nitter (free, no API key)."""
    query = f"${symbol} OR #{symbol} NSE"
    mentions = 0
    sentiment_score = 0.0

    for instance in NITTER_INSTANCES:
        try:
            url = f"{instance}/search?q={requests.utils.quote(query)}&f=tweets"
            resp = requests.get(url, headers=HEADERS, timeout=8)
            if resp.status_code != 200:
                continue
            soup = BeautifulSoup(resp.text, "html.parser")
            tweets = soup.select("div.tweet-content")[:20]
            if not tweets:
                continue

            mentions = len(tweets)
            pos = neg = 0
            for tweet in tweets:
                text = tweet.get_text(strip=True).lower()
                pos += sum(1 for kw in POSITIVE_KW if kw in text)
                neg += sum(1 for kw in NEGATIVE_KW if kw in text)

            total = pos + neg
            sentiment_score = (pos - neg) / total if total > 0 else 0.0
            logger.debug(f"Twitter {symbol}: {mentions} tweets, sentiment={sentiment_score:.2f}")
            break  # success, stop trying instances
        except Exception:
            continue

    return {"mentions": mentions, "sentiment": round(sentiment_score, 3)}


# ── Source 6: Google Trends (pytrends) ───────────────────────────────────────

def fetch_google_trends(symbols: list[str]) -> dict[str, float]:
    """Get relative search interest for stock symbols in India."""
    scores = {}
    try:
        from pytrends.request import TrendReq
        pt = TrendReq(hl="en-IN", tz=330, timeout=(5, 15))
        # Process in batches of 5 (pytrends limit)
        for i in range(0, len(symbols), 5):
            batch = symbols[i:i+5]
            try:
                pt.build_payload(batch, cat=0, timeframe="now 1-d", geo="IN")
                df = pt.interest_over_time()
                if df is not None and not df.empty:
                    for sym in batch:
                        if sym in df.columns:
                            scores[sym] = float(df[sym].mean())
                time.sleep(1)  # rate limit
            except Exception:
                pass
    except ImportError:
        logger.debug("pytrends not installed, skipping Google Trends")
    return scores


# ── Scoring & Deduplication ───────────────────────────────────────────────────

def _aggregate_candidates(raw: list[dict]) -> list[dict]:
    """Merge duplicate symbols, sum scores, collect reasons."""
    merged: dict[str, dict] = {}
    for item in raw:
        sym = item["symbol"].upper().strip()
        if not sym or len(sym) > 15:
            continue
        if sym not in merged:
            merged[sym] = {"symbol": sym, "score": 0.0, "sources": [], "reasons": []}
        merged[sym]["score"] += item.get("score", 1.0)
        merged[sym]["sources"].append(item.get("source", "unknown"))
        merged[sym]["reasons"].append(item.get("reason", ""))

    # Bonus: appearing in multiple sources = stronger signal
    for sym, data in merged.items():
        if len(set(data["sources"])) >= 2:
            data["score"] *= 1.5  # multi-source bonus

    return sorted(merged.values(), key=lambda x: x["score"], reverse=True)


# ── Main Agent ────────────────────────────────────────────────────────────────

class DiscoveryAgent(Agent):
    def __init__(self, config: dict):
        super().__init__("DiscoveryAgent", config)
        self.config_path = "config.yaml"

    def run(self, context: Optional[dict] = None) -> AgentResult:
        return self._result(self.discover())

    def discover(self, top_n: int = 10) -> dict:
        """Run all discovery sources and return ranked candidates."""
        logger.info("DiscoveryAgent: scanning all sources...")
        raw = []

        logger.info("  → NSE movers...")
        raw.extend(fetch_nse_movers())

        logger.info("  → Unusual volume...")
        raw.extend(fetch_unusual_volume())

        logger.info("  → Bulk deals...")
        raw.extend(fetch_bulk_deals())

        logger.info("  → MoneyControl active...")
        raw.extend(fetch_moneycontrol_active())

        candidates = _aggregate_candidates(raw)[:top_n]

        # Enrich with Twitter sentiment for top candidates
        logger.info(f"  → Twitter sentiment for top {min(5, len(candidates))} candidates...")
        for c in candidates[:5]:
            tw = fetch_twitter_sentiment(c["symbol"])
            c["twitter_mentions"] = tw["mentions"]
            c["twitter_sentiment"] = tw["sentiment"]
            if tw["mentions"] > 5:
                c["score"] += tw["mentions"] * 0.1 + tw["sentiment"] * 2

        # Re-sort after Twitter enrichment
        candidates = sorted(candidates, key=lambda x: x["score"], reverse=True)

        # Add to watchlist
        added = self._add_to_watchlist([c["symbol"] for c in candidates[:5]])

        logger.info(f"DiscoveryAgent: found {len(candidates)} candidates, added {len(added)} to watchlist")
        return {
            "candidates": candidates,
            "added_to_watchlist": added,
            "scanned_at": datetime.now().isoformat(),
        }

    def _add_to_watchlist(self, symbols: list[str]) -> list[str]:
        """Append new symbols to config.yaml watchlist."""
        try:
            with open(self.config_path) as f:
                config = yaml.safe_load(f)
            existing = set(config.get("watchlist", []))
            new = [s for s in symbols if s not in existing]
            if new:
                config["watchlist"] = list(existing) + new
                with open(self.config_path, "w") as f:
                    yaml.dump(config, f, default_flow_style=False, allow_unicode=True)
                logger.info(f"Added to watchlist: {new}")
            return new
        except Exception as e:
            logger.error(f"Failed to update watchlist: {e}")
            return []


if __name__ == "__main__":
    from dotenv import load_dotenv
    from core.logger import setup_logging

    load_dotenv()
    with open("config.yaml") as f:
        config = yaml.safe_load(f)
    setup_logging(config)

    agent = DiscoveryAgent(config)
    result = agent.discover(top_n=10)

    print(f"\n{'='*60}")
    print(f"  STOCK DISCOVERY — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*60}")

    candidates = result["candidates"]
    if not candidates:
        print("  No candidates found (markets may be closed or APIs blocked)")
    else:
        print(f"  {'#':<3} {'Symbol':<12} {'Score':<8} {'Twitter':<10} Sources")
        print(f"  {'-'*55}")
        for i, c in enumerate(candidates, 1):
            tw = f"{c.get('twitter_mentions', 0)}tw/{c.get('twitter_sentiment', 0):+.2f}"
            sources = ", ".join(set(c["sources"]))
            print(f"  {i:<3} {c['symbol']:<12} {c['score']:<8.2f} {tw:<10} {sources}")
            for r in c["reasons"][:2]:
                if r:
                    print(f"      └─ {r}")

    print(f"\n  Added to watchlist: {result['added_to_watchlist'] or 'none (all already present)'}")
    print(f"{'='*60}")
