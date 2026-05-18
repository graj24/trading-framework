"""
Stage 3a tests — `core.sentiment_aggregation`.

Each transformation tested in isolation, plus an end-to-end aggregate() check
that combines all of them. No FinBERT, no network — fast and deterministic.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from core.sentiment_aggregation import (
    aggregate, deduplicate, recency_weight, source_weight,
    AggregatedSentiment, DEFAULT_HALF_LIFE_HOURS,
)


# ── source_weight ────────────────────────────────────────────────────────────

def test_source_weight_known_sources():
    assert source_weight("reuters")          == 1.0
    assert source_weight("bloomberg")        == 1.0
    assert source_weight("nse_announcements") == 1.0
    assert source_weight("Economic Times")   == 0.85
    assert source_weight("Yahoo Finance")    == 0.6


def test_source_weight_unknown_falls_back():
    assert source_weight(None)            == 0.5
    assert source_weight("")              == 0.5
    assert source_weight("random_blog")   == 0.5


def test_source_weight_substring_match():
    """Variants like 'Reuters India' or 'reuters_world' should still hit the
    high-trust prior."""
    assert source_weight("Reuters India")   == 1.0
    assert source_weight("reuters_world")   == 1.0
    assert source_weight("Bloomberg Asia")  == 1.0


# ── recency_weight ───────────────────────────────────────────────────────────

def test_recency_weight_fresh_is_full():
    assert recency_weight(0) == 1.0
    assert recency_weight(0.5) > 0.95


def test_recency_weight_half_life():
    """At one half-life, weight is exactly 0.5."""
    assert recency_weight(DEFAULT_HALF_LIFE_HOURS) == pytest.approx(0.5, abs=1e-9)


def test_recency_weight_two_half_lives():
    assert recency_weight(2 * DEFAULT_HALF_LIFE_HOURS) == pytest.approx(0.25, abs=1e-9)


def test_recency_weight_floor_clamps():
    """Very old items don't decay to zero — floor at 0.01."""
    assert recency_weight(1000.0) == 0.01


# ── deduplicate ──────────────────────────────────────────────────────────────

def test_dedup_collapses_near_duplicates():
    """Same story syndicated as close paraphrases must merge into one cluster."""
    items = [
        {"headline": "Reliance Q4 profit jumps 12% beating estimates",        "source": "reuters"},
        {"headline": "Reliance Q4 profit jumps 12% above estimates",          "source": "moneycontrol"},
        {"headline": "Reliance reports Q4 profit jumps 12% beating estimates", "source": "yahoo_finance"},
        {"headline": "Adani Group announces new port acquisition",            "source": "reuters"},
    ]
    out = deduplicate(items, threshold=0.4)
    # First three are close paraphrases — should collapse to 1 cluster.
    assert len(out) == 2, f"expected 2 clusters, got {len(out)}"


def test_dedup_keeps_highest_weight_representative():
    """In a near-duplicate cluster, the wire-source headline must win over
    the aggregator's wording."""
    items = [
        {"headline": "Tata Motors Q3 net profit rises 18% on strong demand",   "source": "yahoo_finance"},
        {"headline": "Tata Motors Q3 net profit rises 18 percent strong demand", "source": "reuters"},
    ]
    out = deduplicate(items, threshold=0.4)
    assert len(out) == 1
    assert out[0]["source"] == "reuters", "Reuters (weight 1.0) should win over Yahoo (0.6)"


def test_dedup_keeps_distinct_stories_separate():
    items = [
        {"headline": "HDFC Bank announces dividend",       "source": "reuters"},
        {"headline": "Infosys signs major cloud deal",     "source": "reuters"},
        {"headline": "TCS opens new innovation hub Pune",  "source": "reuters"},
    ]
    out = deduplicate(items, threshold=0.4)
    assert len(out) == 3, "Three unrelated headlines must stay distinct"


# ── aggregate ────────────────────────────────────────────────────────────────

def test_aggregate_no_items_returns_no_news_quality():
    a = aggregate([])
    assert a.quality == "no_news"
    assert a.sentiment == 0.0
    assert a.n_unique == 0
    assert a.n_raw == 0


def test_aggregate_distinguishes_no_news_from_neutral_news():
    """Two balanced positive+negative items should produce sentiment ≈ 0 but
    quality 'fresh' (or 'stale' / 'low_quality') — NEVER 'no_news'."""
    now = datetime(2025, 6, 1, 12, 0, tzinfo=timezone.utc)
    items = [
        {"headline": "TCS Q1 results beat estimates",         "source": "reuters",
         "sentiment": +0.7, "fetched_at": now.isoformat()},
        {"headline": "TCS hit by labor union strike action",  "source": "reuters",
         "sentiment": -0.7, "fetched_at": now.isoformat()},
    ]
    a = aggregate(items, now=now)
    assert a.quality == "fresh"
    assert abs(a.sentiment) < 0.1
    assert a.n_unique == 2


def test_aggregate_recency_decays_old_negatives():
    """A 2-week-old very-negative headline should not dominate a fresh,
    moderately-positive one — the decay handles that."""
    now = datetime(2025, 6, 1, 12, 0, tzinfo=timezone.utc)
    items = [
        {"headline": "Reliance fraud probe widens scandal",   "source": "reuters",
         "sentiment": -0.9, "fetched_at": (now - timedelta(days=14)).isoformat()},
        {"headline": "Reliance reports better-than-expected Q1 results", "source": "reuters",
         "sentiment": +0.4, "fetched_at": now.isoformat()},
    ]
    a = aggregate(items, now=now)
    assert a.sentiment > 0.0, (
        f"Fresh positive should outweigh 2-week-old negative, got {a.sentiment}"
    )


def test_aggregate_low_quality_when_only_aggregators():
    """If every source is a low-trust aggregator, quality should report
    'low_quality' so the decision pipeline can downweight."""
    now = datetime(2025, 6, 1, 12, 0, tzinfo=timezone.utc)
    items = [
        {"headline": "Stock to watch today: HDFC Bank ABCDEFGH XYZ",
         "source": "google_finance", "sentiment": +0.3, "fetched_at": now.isoformat()},
        {"headline": "Top gainers today: SBIN ITC etc completely separate",
         "source": "yahoo_finance", "sentiment": +0.2, "fetched_at": now.isoformat()},
    ]
    a = aggregate(items, now=now)
    assert a.quality == "low_quality"


def test_aggregate_dedup_does_not_inflate_one_story():
    """Five copies of the same story should not move sentiment 5×."""
    now = datetime(2025, 6, 1, 12, 0, tzinfo=timezone.utc)
    duplicates = [
        {"headline": "Infosys wins large cloud transformation deal worldwide",
         "source": "reuters",        "sentiment": +0.8, "fetched_at": now.isoformat()},
        {"headline": "Infosys wins large cloud transformation deal globally",
         "source": "moneycontrol",   "sentiment": +0.8, "fetched_at": now.isoformat()},
        {"headline": "Infosys wins big cloud transformation deal worldwide",
         "source": "yahoo_finance",  "sentiment": +0.8, "fetched_at": now.isoformat()},
        {"headline": "Infosys wins large cloud transformation deal abroad",
         "source": "google_finance", "sentiment": +0.8, "fetched_at": now.isoformat()},
    ]
    others = [
        {"headline": "TCS announces dividend in line with expectations",
         "source": "reuters", "sentiment": -0.1, "fetched_at": now.isoformat()},
    ]
    a = aggregate(duplicates + others, now=now)
    # Without dedup, sentiment would be ~+0.6 (4 x +0.8 averaged with 1 x -0.1).
    # With dedup, the cluster is one item, so sentiment is closer to mean of
    # two distinct items (~+0.35) and n_unique should be 2.
    assert a.n_unique == 2, f"4 duplicates + 1 distinct should give 2 unique, got {a.n_unique}"
    assert a.n_raw == 5
    assert a.sentiment < 0.6, "Dedup must prevent the same story counting 4x"


def test_aggregate_to_dict_round_trip():
    a = AggregatedSentiment(
        sentiment=0.123, n_unique=3, n_raw=5,
        avg_age_hours=4.5, avg_source_weight=0.9, quality="fresh",
    )
    d = a.to_dict()
    assert d["sentiment"] == 0.123
    assert d["n_unique"] == 3
    assert d["quality"] == "fresh"
