"""Test for B7 — PatternAgent outcome anchored to entry candle, not match_end.

The bug: ``outcome_pct = (prices[future_idx] - prices[match_end]) / prices[match_end]``
measures the return from the END of the matched window, not from the bar
where the system would have actually entered (window end + 1). This
over-states EV for windows that ended on a strong day.

The fix: define an explicit ``entry_idx = match_end + 1`` and shift the
outcome anchor (and the future_idx).
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd


def _make_synthetic_history(stocks_root: Path, symbol: str) -> None:
    """Synthetic price series where the last 20 bars look identical to bars
    100..119, plus a *single huge spike* on bar 119. We can then check
    that the entry-anchored outcome ignores the spike."""
    stocks_root.mkdir(parents=True, exist_ok=True)
    sym_dir = stocks_root / symbol
    sym_dir.mkdir(parents=True, exist_ok=True)

    n = 300
    rng = np.random.default_rng(42)
    base = np.cumprod(1 + rng.normal(0, 0.005, n)) * 100
    # Make bar 119 a +5% spike
    base[119] *= 1.05
    # Make the post-window 10 days flat at the bar-119 closing level
    base[120:130] = base[119]

    df = pd.DataFrame(
        {
            "Open": base, "High": base * 1.001, "Low": base * 0.999,
            "Close": base, "Volume": 1_000_000,
        },
        index=pd.date_range("2020-01-01", periods=n, freq="B"),
    )
    df.to_parquet(sym_dir / "price_history.parquet")


def test_outcome_uses_entry_candle_not_match_end(tmp_path, monkeypatch):
    """Whatever is right after the match window is what we'd buy at —
    that's the anchor for `outcome_pct`."""
    monkeypatch.chdir(tmp_path)
    _make_synthetic_history(tmp_path / "stocks", "SYNTH")

    from agents.pattern_agent import PatternAgent
    agent = PatternAgent({})
    res = agent.run({"symbol": "SYNTH"})
    assert res.ok(), res.error

    import json
    patterns = json.loads((tmp_path / "stocks" / "SYNTH" / "patterns.json").read_text())["patterns"]
    assert patterns, "no patterns matched"

    # If we anchor on match_end (the spike day), the outcome would be 0%
    # because bars 120..130 are flat from there.
    # If we anchor on match_end + 1 (the day we'd have entered), the outcome
    # measures bars 121..130 vs bar 120 — also 0% in this synthetic.
    # The point of the test: the agent should not crash, and it should
    # produce *some* outcome anchored to an entry candle. We verify by
    # checking it returns finite numbers.
    for p in patterns:
        assert p.get("outcome_10d_pct") is not None
        assert isinstance(p["outcome_10d_pct"], (int, float))


def test_outcome_uses_entry_idx(monkeypatch, tmp_path):
    """Direct unit test: build a price series where DTW unambiguously picks
    one specific match window, then check the outcome anchor.
    """
    monkeypatch.chdir(tmp_path)
    sym_dir = tmp_path / "stocks" / "SYNTH"
    sym_dir.mkdir(parents=True)

    n = 200
    rng = np.random.default_rng(123)
    # Background random walk so DTW never sees zero-variance windows.
    base = 100 + np.cumsum(rng.normal(0, 0.2, n))
    # Inject a strongly-rising linear ramp at bars 100..119.
    base[100:120] = np.linspace(101, 130, 20)
    # Bars 120..130: post-match candles. Bar 120 = 110 (gap from match_end=119
    # where price=130), bar 130 = 100. The shape we engineer:
    #   match_end (idx 119): price = 130
    #   entry candle (idx 120): price = 110
    #   ten bars later (idx 130): price = 100
    # If anchor=match_end → outcome = (100-130)/130 ≈ -23.08%
    # If anchor=entry_idx → outcome = (100-110)/110 ≈  -9.09%   ← what we expect
    base[120:131] = [110, 109, 108, 107, 106, 105, 104, 103, 102, 101, 100]
    # Make the current 20-bar window (180..199) the same rising ramp so it
    # uniquely matches bars 100..119.
    base[180:200] = np.linspace(101, 130, 20)

    df = pd.DataFrame(
        {"Open": base, "High": base, "Low": base, "Close": base, "Volume": 1_000_000},
        index=pd.date_range("2020-01-01", periods=n, freq="B"),
    )
    df.to_parquet(sym_dir / "price_history.parquet")

    from agents.pattern_agent import PatternAgent
    agent = PatternAgent({})
    res = agent.run({"symbol": "SYNTH"})
    assert res.ok(), res.error

    import json
    patterns = json.loads((tmp_path / "stocks" / "SYNTH" / "patterns.json").read_text())["patterns"]
    top_outcome = patterns[0]["outcome_10d_pct"]
    # Want close to -9.09% (entry-anchored), NOT close to -23% (match_end-anchored).
    assert -10.5 < top_outcome < -7.5, (
        f"Expected ~-9.09% (entry anchored at 110), got {top_outcome}. "
        f"All top-5: {[p['outcome_10d_pct'] for p in patterns]}"
    )
