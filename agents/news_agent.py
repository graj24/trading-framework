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

import yfinance as yf

from agents.base import Agent, AgentResult
from core.knowledge_base import read_kb, write_kb
from ripple.sentiment_analyzer import SentimentAnalyzer

_finbert = None

def _get_finbert() -> SentimentAnalyzer:
    global _finbert
    if _finbert is None:
        _finbert = SentimentAnalyzer()
    return _finbert


def _score_sentiment_finbert(texts: list[str]) -> float:
    """Use FinBERT to score a list of headlines. Returns -1.0 to +1.0."""
    if not texts:
        return 0.0
    try:
        analyzer = _get_finbert()
        results = analyzer.analyze_batch(texts)
        # Convert FinBERT % scores to -1..+1: (Positive - Negative) / 100
        scores = [(r.get("Positive", 0) - r.get("Negative", 0)) / 100 for r in results]
        return round(sum(scores) / len(scores), 3)
    except Exception as e:
        logger.warning(f"FinBERT scoring failed, falling back to keyword: {e}")
        return None

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


def _fetch_yahoo_news(symbol: str, limit: int = 15) -> list[dict]:
    """Fetch news via Yahoo Finance for NSE-listed stocks."""
    items = []
    try:
        ticker = yf.Ticker(symbol + ".NS")
        for article in (ticker.news or [])[:limit]:
            title = article.get("content", {}).get("title", "")
            pub_date = article.get("content", {}).get("pubDate", "")
            if title:
                items.append({
                    "source": "yahoo_finance",
                    "headline": title,
                    "url": article.get("content", {}).get("canonicalUrl", {}).get("url", ""),
                    "fetched_at": pub_date or datetime.now().isoformat(),
                })
    except Exception as e:
        logger.debug(f"Yahoo Finance news fetch failed for {symbol}: {e}")
    return items


def _scrape_moneycontrol(symbol: str) -> list[dict]:
    """Scrape MoneyControl news for a symbol."""
    return []


def _scrape_economic_times(symbol: str) -> list[dict]:
    """Scrape Economic Times for symbol news."""
    return []


def _scrape_nse_announcements(symbol: str) -> list[dict]:
    """Fetch NSE corporate announcements via API."""
    return []


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
        """Fetch, score, and store news for a symbol.

        Stage 3a: aggregation goes through `core.sentiment_aggregation` so
        sentiment respects source weighting, recency decay, and deduplication
        of syndicated stories. The result also reports a `quality` field
        distinguishing "no_news" / "stale" / "low_quality" / "fresh" — the
        decision pipeline should treat sentiment differently in each case.
        """
        from core.sentiment_aggregation import aggregate as agg_sentiment

        all_news = _fetch_yahoo_news(symbol)
        relevant = all_news

        # Per-item FinBERT scoring (single batch call).
        headlines = [n["headline"] for n in relevant]
        finbert_score = _score_sentiment_finbert(headlines)

        # Distribute the batch FinBERT result across items proportionally to
        # their per-headline keyword sentiment, so the aggregation receives
        # heterogeneous per-item scores. (This is a coarse approximation —
        # ideally we'd call FinBERT per-headline, but the current pipeline
        # uses a single batch for cost. The aggregation logic itself doesn't
        # rely on this trick; it just needs a per-item sentiment value.)
        for item in relevant:
            item["tier"] = _classify_tier(item["headline"])
            item["sentiment"] = _score_sentiment(item["headline"])

        # If FinBERT batch produced an aggregate, blend it in as a global bias.
        if finbert_score is not None and relevant:
            for item in relevant:
                # 60% per-item keyword score, 40% global FinBERT signal.
                item["sentiment"] = 0.6 * item["sentiment"] + 0.4 * finbert_score

        agg = agg_sentiment(relevant)

        # Worst tier (lowest = most urgent).
        tiers = [n["tier"] for n in relevant if n.get("tier")]
        worst_tier = min(tiers) if tiers else None

        # Persist to KB (append, dedup by headline — keep simple existing logic).
        existing = read_kb(symbol, "news_history.json")
        existing_headlines = {n["headline"] for n in existing.get("news", [])}
        new_items = [n for n in relevant if n["headline"] not in existing_headlines]
        all_stored = existing.get("news", []) + new_items
        write_kb(symbol, "news_history.json", {
            "news": all_stored[-500:],
            "updated_at": datetime.now().isoformat(),
            "last_aggregation": agg.to_dict(),
        })

        top_headlines = [n["headline"] for n in agg.items_used[:3]]
        logger.info(
            f"NewsAgent {symbol}: {agg.n_raw} raw → {agg.n_unique} unique, "
            f"sentiment={agg.sentiment:.2f}, quality={agg.quality}, tier={worst_tier}"
        )

        return {
            "symbol":       symbol,
            "sentiment":    round(agg.sentiment, 3),
            "tier":         worst_tier,
            "news_count":   agg.n_unique,
            "news_raw":     agg.n_raw,
            "headlines":    top_headlines,
            "quality":      agg.quality,
            "avg_age_hours":     agg.avg_age_hours,
            "avg_source_weight": agg.avg_source_weight,
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
