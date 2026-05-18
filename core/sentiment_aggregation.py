"""
Sentiment aggregation rebuild (Stage 3a).

The legacy aggregation in news_agent was a flat mean of (Pos − Neg)/100 over
all FinBERT-scored headlines. That has four real problems:

  1. No source weighting — a story scraped from a low-reliability aggregator
     counts as much as a Bloomberg/Reuters wire.
  2. No deduplication — the same story syndicated across 5 sites gets 5×
     weight in the average.
  3. No recency decay — a 2-week-old "earnings beat" gets equal weight to a
     headline from 30 minutes ago.
  4. Same output for "no news" and "neutral news" (both ~ 0.0) — the system
     can't tell the absence of signal from a balanced one.

This module fixes all four, with no new dependencies. Tests validate each
transformation independently from FinBERT/network so the aggregation is
deterministic and reviewable.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable, Optional


# ── Source weighting ─────────────────────────────────────────────────────────

# Higher = more trusted. Numbers are loose priors — they can be tuned, but
# the relative ordering matters more than the absolute scale.
SOURCE_WEIGHTS: dict[str, float] = {
    # Wire services and primary exchanges
    "reuters":            1.0,
    "bloomberg":          1.0,
    "nse_announcements":  1.0,
    "bse":                1.0,

    # Established financial press
    "economic_times":     0.85,
    "businessstandard":   0.85,
    "moneycontrol":       0.75,
    "livemint":           0.75,

    # Aggregators and broader feeds
    "yahoo_finance":      0.6,
    "google_finance":     0.5,

    # Default for unknown sources
    "_default":           0.5,
}


def source_weight(source: str | None) -> float:
    """Return the prior weight for a source; defaults to 0.5 if unknown."""
    if not source:
        return SOURCE_WEIGHTS["_default"]
    key = source.lower().strip().replace("-", "_").replace(" ", "_")
    if key in SOURCE_WEIGHTS:
        return SOURCE_WEIGHTS[key]
    # Substring match for variants like "reuters_india"
    for known in SOURCE_WEIGHTS:
        if known != "_default" and known in key:
            return SOURCE_WEIGHTS[known]
    return SOURCE_WEIGHTS["_default"]


# ── Recency decay ────────────────────────────────────────────────────────────

DEFAULT_HALF_LIFE_HOURS = 12.0


def recency_weight(age_hours: float, half_life_hours: float = DEFAULT_HALF_LIFE_HOURS
                   ) -> float:
    """Exponential decay: fresh news has weight 1.0, halves every `half_life`
    hours. Output clamped to [0.01, 1.0] so very old items don't disappear.
    """
    if age_hours <= 0:
        return 1.0
    w = math.pow(0.5, age_hours / half_life_hours)
    return max(0.01, min(1.0, w))


def _age_hours_from(item: dict, now: Optional[datetime] = None) -> float:
    """Best-effort age in hours from an item's 'fetched_at' timestamp.
    Returns 0.0 if the timestamp is missing or unparseable (treat as fresh).
    """
    ts = item.get("fetched_at")
    if not ts:
        return 0.0
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except Exception:
        return 0.0
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return max(0.0, (now - dt).total_seconds() / 3600.0)


# ── Deduplication ────────────────────────────────────────────────────────────

_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")


def _shingle_tokens(text: str, n: int = 2) -> set[str]:
    """Token-level n-gram shingles used for fuzzy deduplication. Lowercased,
    stripped of punctuation. Two headlines telling the same story will share
    a high fraction of shingles even if wording differs slightly.

    Default n=2 (bigrams) is more permissive than trigrams for short news
    headlines where word substitutions ('wins' vs 'signs', 'globally' vs
    'worldwide') would otherwise prevent clustering.
    """
    tokens = [t.lower() for t in _TOKEN_RE.findall(text)]
    if len(tokens) < n:
        return {" ".join(tokens)} if tokens else set()
    return {" ".join(tokens[i:i + n]) for i in range(len(tokens) - n + 1)}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def deduplicate(items: Iterable[dict], threshold: float = 0.5) -> list[dict]:
    """Cluster items by Jaccard similarity on 3-gram shingles; keep the
    highest-source-weight representative of each cluster.

    `threshold` of 0.5 means two headlines sharing roughly half their 3-grams
    are treated as the same story. Empirically tight enough to merge wire
    syndication while leaving distinct stories separate.
    """
    items_l = list(items)
    if not items_l:
        return []
    shingles = [_shingle_tokens(i.get("headline", "")) for i in items_l]
    cluster: list[Optional[int]] = [None] * len(items_l)
    next_id = 0
    for i, s_i in enumerate(shingles):
        if cluster[i] is not None:
            continue
        cluster[i] = next_id
        for j in range(i + 1, len(items_l)):
            if cluster[j] is not None:
                continue
            if _jaccard(s_i, shingles[j]) >= threshold:
                cluster[j] = next_id
        next_id += 1

    # Keep the highest-source-weight item from each cluster (tiebreak: longest
    # headline — usually the most informative wording).
    by_cluster: dict[int, list[int]] = {}
    for idx, cid in enumerate(cluster):
        by_cluster.setdefault(cid, []).append(idx)

    out: list[dict] = []
    for cid, idxs in by_cluster.items():
        best = max(idxs, key=lambda k: (
            source_weight(items_l[k].get("source")),
            len(items_l[k].get("headline", "")),
        ))
        out.append(items_l[best])
    return out


# ── Aggregation ──────────────────────────────────────────────────────────────

@dataclass
class AggregatedSentiment:
    """Output of aggregate(). Use .quality to gate decisions."""
    sentiment: float                 # -1..+1, 0.0 if no items
    n_unique:  int                   # post-dedup item count
    n_raw:     int                   # pre-dedup item count
    avg_age_hours: float
    avg_source_weight: float
    quality: str                     # "no_news" | "low_quality" | "fresh" | "stale"
    items_used: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "sentiment":         round(self.sentiment, 4),
            "n_unique":          self.n_unique,
            "n_raw":             self.n_raw,
            "avg_age_hours":     round(self.avg_age_hours, 2),
            "avg_source_weight": round(self.avg_source_weight, 3),
            "quality":           self.quality,
        }


def aggregate(items: Iterable[dict],
              now: Optional[datetime] = None,
              dedup_threshold: float = 0.5,
              half_life_hours: float = DEFAULT_HALF_LIFE_HOURS,
              ) -> AggregatedSentiment:
    """Apply dedup + source-weighting + recency decay to a list of news items.

    Each item is expected to have:
      - 'headline' (str)
      - 'source' (str, optional — defaults to "_default")
      - 'fetched_at' (ISO-8601 str, optional)
      - 'sentiment' (-1..+1, the per-item sentiment from FinBERT or keyword scorer)

    Output `sentiment` is the weight-averaged item sentiment.
    `quality` distinguishes:
      - "no_news":     n_unique == 0
      - "low_quality": only low-weight sources (<0.6 average) — be cautious
      - "stale":       freshest item is >24h old
      - "fresh":       at least one fresh, decent-weight item present
    """
    raw = list(items)
    if not raw:
        return AggregatedSentiment(0.0, 0, 0, 0.0, 0.0, "no_news")

    deduped = deduplicate(raw, threshold=dedup_threshold)

    weights: list[float] = []
    sentiments: list[float] = []
    ages: list[float] = []
    src_weights: list[float] = []

    for item in deduped:
        sw = source_weight(item.get("source"))
        ah = _age_hours_from(item, now=now)
        rw = recency_weight(ah, half_life_hours=half_life_hours)
        s  = float(item.get("sentiment", 0.0))
        weights.append(sw * rw)
        sentiments.append(s)
        ages.append(ah)
        src_weights.append(sw)

    total_w = sum(weights)
    if total_w == 0:
        weighted_sent = 0.0
    else:
        weighted_sent = sum(s * w for s, w in zip(sentiments, weights)) / total_w

    avg_age = sum(ages) / len(ages)
    avg_src = sum(src_weights) / len(src_weights)

    if avg_src < 0.6:
        quality = "low_quality"
    else:
        # Freshest item dominates the freshness call.
        min_age = min(ages) if ages else 999.0
        quality = "fresh" if min_age < 24.0 else "stale"

    return AggregatedSentiment(
        sentiment=weighted_sent,
        n_unique=len(deduped),
        n_raw=len(raw),
        avg_age_hours=avg_age,
        avg_source_weight=avg_src,
        quality=quality,
        items_used=deduped,
    )
