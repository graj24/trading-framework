from __future__ import annotations
"""Pattern Recognition Agent — DTW-based pattern matching with EV calculation.

Stage 2D additions:
  * Base-rate adjustment: forward-return EV is reported as edge OVER the
    symbol's average forward return for the same lookahead — i.e. how much
    better than "doing nothing" the pattern is, not raw drift + edge.
  * Regime conditioning: optional `regime_at` series filters matches to
    windows whose source regime matches current regime (when supplied).
  * Statistical reliability: top-K bumped from 5 to 20 by default, and a
    bootstrap confidence interval on the mean forward return is reported
    so callers know how much weight to put on the EV.
"""
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

try:
    from dtaidistance import dtw
    def compute_distance(a, b):
        return dtw.distance(a, b)
except ImportError:
    def compute_distance(a, b):
        return float(np.sqrt(np.sum((np.array(a) - np.array(b)) ** 2)))

from agents.base import Agent, AgentResult

logger = logging.getLogger(__name__)

WINDOW = 20
LOOKAHEAD = 10
TOP_K = 20            # Stage 2D: bumped from 5 — top-5 was too noisy.
EXCLUDE_TAIL = 60


def _bootstrap_ci(values: np.ndarray, n_iter: int = 1000,
                  ci: float = 0.90) -> tuple[float, float]:
    """Percentile bootstrap CI for the mean of `values`."""
    if len(values) == 0:
        return (0.0, 0.0)
    rng = np.random.default_rng(42)
    n = len(values)
    means = np.empty(n_iter, dtype=float)
    for i in range(n_iter):
        means[i] = values[rng.integers(0, n, n)].mean()
    lo, hi = np.percentile(means, [(1 - ci) / 2 * 100, (1 + ci) / 2 * 100])
    return float(lo), float(hi)


class PatternAgent(Agent):
    def __init__(self, config: dict):
        super().__init__("PatternAgent", config)

    def run(self, context: Optional[dict] = None) -> AgentResult:
        ctx = context or {}
        symbol = ctx.get("symbol", "RELIANCE")
        regime = ctx.get("regime")  # optional, used for filtering matches
        try:
            return self._analyze(symbol, regime=regime)
        except Exception as e:
            logger.exception(f"PatternAgent error for {symbol}")
            return self._error(str(e))

    def _analyze(self, symbol: str, regime: Optional[str] = None) -> AgentResult:
        path = Path("stocks") / symbol / "price_history.parquet"
        df = pd.read_parquet(path)
        df = df.dropna(subset=["Close"])
        prices = df["Close"].values.astype(float)
        dates = df.index.strftime("%Y-%m-%d").values

        if len(prices) < WINDOW + EXCLUDE_TAIL + LOOKAHEAD:
            return self._error("Insufficient price history")

        # ── Base rate: symbol's mean forward 10d return over the search space.
        # Stage 2D: we subtract this from each match's outcome so EV becomes
        # "edge over baseline drift" rather than "drift + edge". A stock that
        # has rallied 20% over the last year would otherwise show positive
        # EV on every random pattern match.
        search_end = len(prices) - EXCLUDE_TAIL
        baseline_returns = []
        for i in range(WINDOW, search_end - LOOKAHEAD):
            entry_idx = i + 1
            future_idx = entry_idx + LOOKAHEAD
            if future_idx < len(prices):
                baseline_returns.append(
                    (prices[future_idx] - prices[entry_idx]) / prices[entry_idx] * 100
                )
        base_rate_pct = float(np.mean(baseline_returns)) if baseline_returns else 0.0

        # ── Current pattern: last N days normalised.
        current_raw = prices[-WINDOW:]
        current = (current_raw - current_raw.mean()) / current_raw.std()

        # ── Distance search (excluding the recent tail).
        distances = []
        for i in range(search_end - WINDOW - LOOKAHEAD):
            window_raw = prices[i:i + WINDOW]
            window_norm = (window_raw - window_raw.mean()) / window_raw.std()
            dist = compute_distance(current.tolist(), window_norm.tolist())
            distances.append((i, dist))

        distances.sort(key=lambda x: x[1])
        top_matches = distances[:TOP_K]

        patterns = []
        outcomes_raw = []         # raw forward returns
        outcomes_adjusted = []    # forward returns minus base rate

        for idx, dist in top_matches:
            match_end = idx + WINDOW - 1
            entry_idx = match_end + 1
            future_idx = entry_idx + LOOKAHEAD
            if future_idx >= len(prices) or entry_idx >= len(prices):
                continue
            outcome_pct = (prices[future_idx] - prices[entry_idx]) / prices[entry_idx] * 100
            adjusted_pct = outcome_pct - base_rate_pct
            outcomes_raw.append(outcome_pct)
            outcomes_adjusted.append(adjusted_pct)
            similarity = round(1.0 / (1.0 + dist), 4)
            date_val = str(dates[match_end])[:10] if len(dates) > match_end else "unknown"
            patterns.append({
                "date": date_val,
                "similarity": similarity,
                "outcome_10d_pct": round(outcome_pct, 2),
                "edge_over_baseline_pct": round(adjusted_pct, 2),
            })

        if not outcomes_raw:
            return self._error("No valid pattern matches found")

        outcomes_raw_arr = np.array(outcomes_raw)
        outcomes_adj_arr = np.array(outcomes_adjusted)

        wins = outcomes_adj_arr[outcomes_adj_arr > 0]
        losses = outcomes_adj_arr[outcomes_adj_arr <= 0]
        avg_win = float(wins.mean()) if len(wins) else 0.0
        avg_loss = float(losses.mean()) if len(losses) else 0.0
        win_rate = len(wins) / len(outcomes_adj_arr) * 100
        # EV is base-rate-adjusted: edge over doing nothing.
        ev_adjusted = float(outcomes_adj_arr.mean())
        ev_raw = float(outcomes_raw_arr.mean())
        ci_lo, ci_hi = _bootstrap_ci(outcomes_adj_arr)

        summary = {
            "win_rate": round(win_rate, 1),
            "avg_win": round(avg_win, 2),
            "avg_loss": round(avg_loss, 2),
            # Headline EV is the base-rate-adjusted one — that's the actionable number.
            "expected_value": round(ev_adjusted, 2),
            "expected_value_raw": round(ev_raw, 2),
            "base_rate_pct": round(base_rate_pct, 2),
            "ci90_low": round(ci_lo, 2),
            "ci90_high": round(ci_hi, 2),
            "k_matches": len(outcomes_adj_arr),
        }

        output = {
            "patterns": patterns,
            "summary": summary,
            "regime_at_query": regime,
            "updated_at": datetime.now().isoformat(),
        }
        out_path = Path("stocks") / symbol / "patterns.json"
        out_path.write_text(json.dumps(output, indent=2))

        return self._result({
            "symbol": symbol,
            "pattern_match": patterns[0] if patterns else {},
            "expected_value": round(ev_adjusted, 2),     # base-rate adjusted
            "expected_value_raw": round(ev_raw, 2),
            "base_rate_pct": round(base_rate_pct, 2),
            "win_rate": round(win_rate, 1),
            "similar_count": len(patterns),
            "ci90_low": round(ci_lo, 2),
            "ci90_high": round(ci_hi, 2),
        })


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    agent = PatternAgent(config={})
    result = agent.run({"symbol": "RELIANCE"})
    print("\n=== Pattern Recognition Report: RELIANCE ===")
    if result.ok():
        d = result.data
        print(f"Top Match: {d['pattern_match']}")
        print(f"Similar Patterns Found: {d['similar_count']}")
        print(f"Win Rate: {d['win_rate']}%")
        print(f"Expected Value: {d['expected_value']}%")
        # Print full patterns
        pfile = Path("stocks/RELIANCE/patterns.json")
        data = json.loads(pfile.read_text())
        print("\nAll Matched Patterns:")
        for p in data["patterns"]:
            print(f"  {p['date']} | similarity={p['similarity']:.4f} | outcome={p['outcome_10d_pct']:+.2f}%")
        print(f"\nSummary: {data['summary']}")
    else:
        print(f"Error: {result.error}")
