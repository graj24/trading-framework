"""
News Agent — scrapes financial news, scores sentiment, categorizes events.

TIER 1 (immediate exit): fraud, CEO/CFO resignation, accident, regulatory action, bankruptcy
TIER 2 (re-evaluate): earnings miss, guidance cut, analyst downgrade
TIER 3 (monitor): minor news, upgrades, sector news

Sentiment: -1.0 (very negative) to +1.0 (very positive)
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Optional

import requests
from bs4 import BeautifulSoup

from agents.base import Agent, AgentResult
from core.knowledge_base import read_kb, write_kb

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}
TIMEOUT = 8

POSITIVE_KW = {"beat", "profit", "growth", "upgrade", "acquisition", "expansion", "record", "strong",
               "surge", "rally", "gain", "rise", "positive", "outperform", "buy", "bullish", "win",
               "award", "launch", "partnership", "dividend", "buyback"}

NEGATIVE_KW = {"loss", "fraud", "resign", "accident", "miss", "downgrade", "penalty", "shutdown",
               "bankrupt", "decline", "fall", "drop", "negative", "underperform", "sell", "bearish",
               "probe", "investigation", "fine", "default", "debt", "layoff", "cut", "warning"}

TIER1_KW = {"fraud", "resign", "accident", "bankrupt", "regulatory", "ed ", "cbi", "sebi action",
            "arrested", "scam", "scandal", "shutdown", "insolvency", "nclt", "default"}

TIER2_KW = {"miss", "downgrade", "guidance cut", "loss", "below estimate", "disappoints",
            "management change", "ceo change", "cfo change", "profit warning"}


def _score_sentiment(text: str) -> float:
    text_lower = text.lower()
    pos = sum(1 for kw in POSITIVE_KW if kw in text_lower)
    neg = sum(1 for kw in NEGATIVE_KW if kw in text_lower)
    total = pos + neg
    if total == 0:
        return 0.0
    return max(-1.0, min(1.0, (pos - neg) / total))


def _classify_tier(text: str) -> Optional[int]:
    text_lower = text.lower()
    if any(kw in text_lower for kw in TIER1_KW):
        return 1
    if any(kw in text_lower for kw in TIER2_KW):
        return 2
    return 3


def _scrape_moneycontrol(symbol: str) -> list[dict]:
    """Scrape MoneyControl news for a symbol."""
    items = []
    try:
        url = f"https://www.moneycontrol.com/stocks/company_info/stock_news.php?sc_id={symbol}&scat=&pageno=1&next=0&type=4&search_str="
        resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup.select("li.clearfix")[:10]:
            a = tag.find("a")
            if a and a.get_text(strip=True):
                items.append({
                    "source": "moneycontrol",
                    "headline": a.get_text(strip=True),
                    "url": a.get("href", ""),
                    "fetched_at": datetime.now().isoformat(),
                })
    except Exception as e:
        logger.debug(f"MoneyControl scrape failed: {e}")
    return items


def _scrape_economic_times(symbol: str) -> list[dict]:
    """Scrape Economic Times for symbol news."""
    items = []
    try:
        url = f"https://economictimes.indiatimes.com/topic/{symbol.lower()}"
        resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup.select("div.eachStory h3")[:10]:
            a = tag.find("a")
            if a and a.get_text(strip=True):
                items.append({
                    "source": "economic_times",
                    "headline": a.get_text(strip=True),
                    "url": "https://economictimes.indiatimes.com" + a.get("href", ""),
                    "fetched_at": datetime.now().isoformat(),
                })
    except Exception as e:
        logger.debug(f"Economic Times scrape failed: {e}")
    return items


def _scrape_nse_announcements(symbol: str) -> list[dict]:
    """Fetch NSE corporate announcements via API."""
    items = []
    try:
        url = f"https://www.nseindia.com/api/corp-announcements?index=equities&symbol={symbol}"
        session = requests.Session()
        # Prime cookies
        session.get("https://www.nseindia.com", headers=HEADERS, timeout=TIMEOUT)
        resp = session.get(url, headers=HEADERS, timeout=TIMEOUT)
        data = resp.json()
        for item in (data if isinstance(data, list) else [])[:10]:
            headline = item.get("subject", item.get("desc", ""))
            if headline:
                items.append({
                    "source": "nse",
                    "headline": headline,
                    "url": "",
                    "fetched_at": datetime.now().isoformat(),
                })
    except Exception as e:
        logger.debug(f"NSE announcements failed: {e}")
    return items


class NewsAgent(Agent):
    def __init__(self, config: dict):
        super().__init__("NewsAgent", config)

    def run(self, context: Optional[dict] = None) -> AgentResult:
        symbol = (context or {}).get("symbol")
        if not symbol:
            return self._error("No symbol in context")

        result = self.analyze(symbol)
        return self._result(result)

    def analyze(self, symbol: str) -> dict:
        """Fetch, score, and store news for a symbol."""
        all_news = []
        all_news.extend(_scrape_moneycontrol(symbol))
        all_news.extend(_scrape_economic_times(symbol))
        all_news.extend(_scrape_nse_announcements(symbol))

        # Filter: only news mentioning the symbol
        relevant = [
            n for n in all_news
            if symbol.lower() in n["headline"].lower() or n["source"] == "nse"
        ]

        # Score each item
        for item in relevant:
            item["sentiment"] = _score_sentiment(item["headline"])
            item["tier"] = _classify_tier(item["headline"])

        # Aggregate sentiment
        sentiments = [n["sentiment"] for n in relevant]
        avg_sentiment = sum(sentiments) / len(sentiments) if sentiments else 0.0

        # Worst tier (lowest number = most urgent)
        tiers = [n["tier"] for n in relevant if n.get("tier")]
        worst_tier = min(tiers) if tiers else None

        # Persist to knowledge base (append, deduplicate by headline)
        existing = read_kb(symbol, "news_history.json")
        existing_headlines = {n["headline"] for n in existing.get("news", [])}
        new_items = [n for n in relevant if n["headline"] not in existing_headlines]
        all_stored = existing.get("news", []) + new_items
        write_kb(symbol, "news_history.json", {
            "news": all_stored[-500:],  # keep last 500
            "updated_at": datetime.now().isoformat(),
        })

        top_headlines = [n["headline"] for n in relevant[:3]]
        logger.info(f"NewsAgent {symbol}: {len(relevant)} items, sentiment={avg_sentiment:.2f}, tier={worst_tier}")

        return {
            "symbol": symbol,
            "sentiment": round(avg_sentiment, 3),
            "tier": worst_tier,
            "news_count": len(relevant),
            "headlines": top_headlines,
        }

    def monitor_open_positions(self, symbols: list[str]) -> dict[str, Optional[int]]:
        """Check news for all open positions. Returns {symbol: tier} for TIER 1/2 events."""
        alerts = {}
        for symbol in symbols:
            result = self.analyze(symbol)
            tier = result.get("tier")
            if tier in (1, 2):
                alerts[symbol] = tier
        return alerts


if __name__ == "__main__":
    import yaml
    from dotenv import load_dotenv
    from core.logger import setup_logging

    load_dotenv()
    with open("config.yaml") as f:
        config = yaml.safe_load(f)
    setup_logging(config)

    agent = NewsAgent(config)
    for sym in ["RELIANCE", "INFY"]:
        result = agent.analyze(sym)
        print(f"\n{sym}:")
        print(f"  Sentiment : {result['sentiment']}")
        print(f"  Tier      : {result['tier']}")
        print(f"  News count: {result['news_count']}")
        for h in result["headlines"]:
            print(f"  - {h}")
