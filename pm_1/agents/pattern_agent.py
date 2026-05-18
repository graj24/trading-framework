from __future__ import annotations
"""Pattern Recognition Agent — DTW-based pattern matching with EV calculation."""
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
TOP_K = 20  # Stage 2D: bumped from 5 for statistical reliability
EXCLUDE_TAIL = 60


class PatternAgent(Agent):
    def __init__(self, config: dict):
        super().__init__("PatternAgent", config)

    def run(self, context: Optional[dict] = None) -> AgentResult:
        symbol = (context or {}).get("symbol", "RELIANCE")
        try:
            return self._analyze(symbol)
        except Exception as e:
            logger.exception(f"PatternAgent error for {symbol}")
            return self._error(str(e))

    def _analyze(self, symbol: str) -> AgentResult:
        path = Path("stocks") / symbol / "price_history.parquet"
        df = pd.read_parquet(path)
        df = df.dropna(subset=["Close"])
        prices = df["Close"].values.astype(float)
        dates = df.index.strftime("%Y-%m-%d").values

        if len(prices) < WINDOW + EXCLUDE_TAIL + LOOKAHEAD:
            return self._error("Insufficient price history")

        # Current pattern: last N days normalized
        current_raw = prices[-WINDOW:]
        current = (current_raw - current_raw.mean()) / current_raw.std()

        # Search space: exclude last EXCLUDE_TAIL days
        search_end = len(prices) - EXCLUDE_TAIL
        distances = []

        for i in range(search_end - WINDOW - LOOKAHEAD):
            window_raw = prices[i:i + WINDOW]
            window_norm = (window_raw - window_raw.mean()) / window_raw.std()
            dist = compute_distance(current.tolist(), window_norm.tolist())
            distances.append((i, dist))

        distances.sort(key=lambda x: x[1])
        top_matches = distances[:TOP_K]

        # Compute outcomes
        patterns = []
        outcomes = []

        for idx, dist in top_matches:
            match_end = idx + WINDOW - 1
            # B7: entry happens on the bar AFTER the matched window; that's
            # the right anchor for measuring the trade outcome. Anchoring
            # on `match_end` over-states EV when the window ended on a
            # strong day.
            entry_idx = match_end + 1
            future_idx = entry_idx + LOOKAHEAD
            if future_idx >= len(prices) or entry_idx >= len(prices):
                continue
            outcome_pct = (prices[future_idx] - prices[entry_idx]) / prices[entry_idx] * 100
            outcomes.append(outcome_pct)
            similarity = round(1.0 / (1.0 + dist), 4)
            date_val = str(dates[match_end])[:10] if len(dates) > match_end else "unknown"
            patterns.append({
                "date": date_val,
                "similarity": similarity,
                "outcome_10d_pct": round(outcome_pct, 2)
            })

        if not outcomes:
            return self._error("No valid pattern matches found")

        # Compute EV
        wins = [o for o in outcomes if o > 0]
        losses = [o for o in outcomes if o <= 0]
        total = len(outcomes)
        avg_win = sum(wins) / len(wins) if wins else 0.0
        avg_loss = sum(losses) / len(losses) if losses else 0.0
        win_rate = len(wins) / total * 100
        ev = (len(wins) / total * avg_win) + (len(losses) / total * avg_loss)

        summary = {
            "win_rate": round(win_rate, 1),
            "avg_win": round(avg_win, 2),
            "avg_loss": round(avg_loss, 2),
            "expected_value": round(ev, 2)
        }

        # Save to JSON
        output = {
            "patterns": patterns,
            "summary": summary,
            "updated_at": datetime.now().isoformat()
        }
        out_path = Path("stocks") / symbol / "patterns.json"
        out_path.write_text(json.dumps(output, indent=2))

        return self._result({
            "symbol": symbol,
            "pattern_match": patterns[0] if patterns else {},
            "expected_value": round(ev, 2),
            "win_rate": round(win_rate, 1),
            "similar_count": len(patterns)
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
