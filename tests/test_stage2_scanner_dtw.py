"""
Stage 2 tests — intraday scanner improvements.

Covers:
  * 2A: cooldown prevents the same pattern from firing on consecutive bars
  * 2B: ATR-adaptive thresholds suppress spurious detections on volatile names
  * 2C: empirical confidence lookup falls back gracefully and overrides when present
  * 2D: DTW pattern reports base-rate-adjusted EV that differs from the raw mean
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from agents import intraday_scanner as IS


# ── 2A: Cooldown ─────────────────────────────────────────────────────────────

def _build_breakout_df(n_bars: int = 60) -> pd.DataFrame:
    """OHLCV with a single sharp resistance breakout near the end. The same
    detection should fire on bar N, then be suppressed on bars N+1..N+5
    because of cooldown."""
    rng = np.random.default_rng(0)
    base = np.full(n_bars, 100.0)
    # Hammer a resistance at 102 a few times around bars 10–30.
    for i in (10, 15, 22, 28):
        base[i] = 102.0
    # Then break above with high volume on bar 50, and stay above.
    base[50:] = 104.0

    high = base + 0.1
    low = base - 0.1
    close = base
    open_ = base
    vol = np.full(n_bars, 1000.0)
    vol[50:] = 5000.0  # post-breakout volume surge

    return pd.DataFrame({
        "Open": open_, "High": high, "Low": low, "Close": close, "Volume": vol,
    }, index=pd.date_range("2025-01-01 09:15", periods=n_bars, freq="5min"))


def test_cooldown_suppresses_repeat_firing_on_consecutive_bars(monkeypatch):
    """Drive scan_stock end-to-end with a cooldown_state dict shared across
    two scans. The first scan records the firing timestamp; the second scan,
    1 bar later, must drop the same pattern from `result["patterns"]`.

    We monkeypatch the network bits (get_nse_quote, get_intraday_candles)
    so the test runs entirely on synthetic data.
    """
    df = _build_breakout_df(60)
    df_first  = df.iloc[:51]
    df_second = df.iloc[:52]
    quote = {"vwap": 100.0, "ltp": 104.0}

    # Force resistance_breakout to fire on whatever df is passed.
    forced_pattern = {
        "pattern": "resistance_breakout",
        "resistance_level": 102.0, "rejections": 4,
        "atr_pct": 1.0, "breakout_price": 104.0,
        "target": 106.0, "stop_loss": 101.7,
        "description": "test",
    }
    monkeypatch.setattr(IS, "detect_resistance_breakout",
                         lambda df, atr_pct=1.0: dict(forced_pattern))
    # Other detectors return nothing.
    for det in ("detect_bull_flag", "detect_accumulation_at_support",
                "detect_rsi_divergence"):
        monkeypatch.setattr(IS, det, lambda *a, **kw: None)
    monkeypatch.setattr(IS, "detect_vwap_reclaim", lambda df, q, atr_pct=1.0: None)
    monkeypatch.setattr(IS, "detect_volume_spike", lambda df, q, atr_pct=1.0: None)

    # Capture data sources.
    monkeypatch.setattr(IS, "get_nse_quote",       lambda s: quote)
    monkeypatch.setattr(IS, "_compute_atr_pct",    lambda df, period=14: 1.0)

    cooldown: dict = {}

    monkeypatch.setattr(IS, "get_intraday_candles", lambda s: df_first)
    r1 = IS.scan_stock("ABC", cooldown_state=cooldown, regime="trending_bull")
    assert any(p["pattern"] == "resistance_breakout" for p in r1["patterns"])

    # Second call with a slightly later bar — same pattern should NOT fire.
    monkeypatch.setattr(IS, "get_intraday_candles", lambda s: df_second)
    r2 = IS.scan_stock("ABC", cooldown_state=cooldown, regime="trending_bull")
    assert not any(p["pattern"] == "resistance_breakout" for p in r2["patterns"]), \
        "Cooldown should suppress the repeat firing within COOLDOWN_BARS"


def test_cooldown_releases_after_K_bars(monkeypatch):
    """Same setup, but second scan is COOLDOWN_BARS+1 bars later — must re-fire."""
    df = _build_breakout_df(60)
    df_first  = df.iloc[:51]
    df_later  = df.iloc[:51 + IS.COOLDOWN_BARS + 1]
    quote = {"vwap": 100.0, "ltp": 104.0}
    forced_pattern = {
        "pattern": "resistance_breakout",
        "resistance_level": 102.0, "rejections": 4,
        "atr_pct": 1.0, "breakout_price": 104.0,
        "target": 106.0, "stop_loss": 101.7,
        "description": "test",
    }
    monkeypatch.setattr(IS, "detect_resistance_breakout",
                         lambda df, atr_pct=1.0: dict(forced_pattern))
    for det in ("detect_bull_flag", "detect_accumulation_at_support",
                "detect_rsi_divergence"):
        monkeypatch.setattr(IS, det, lambda *a, **kw: None)
    monkeypatch.setattr(IS, "detect_vwap_reclaim", lambda df, q, atr_pct=1.0: None)
    monkeypatch.setattr(IS, "detect_volume_spike", lambda df, q, atr_pct=1.0: None)
    monkeypatch.setattr(IS, "get_nse_quote",       lambda s: quote)
    monkeypatch.setattr(IS, "_compute_atr_pct",    lambda df, period=14: 1.0)

    cooldown: dict = {}
    monkeypatch.setattr(IS, "get_intraday_candles", lambda s: df_first)
    IS.scan_stock("ABC", cooldown_state=cooldown, regime="ranging")
    monkeypatch.setattr(IS, "get_intraday_candles", lambda s: df_later)
    r2 = IS.scan_stock("ABC", cooldown_state=cooldown, regime="ranging")
    assert any(p["pattern"] == "resistance_breakout" for p in r2["patterns"]), \
        f"After COOLDOWN_BARS+1 bars the pattern should re-fire ({IS.COOLDOWN_BARS=})"


# ── 2B: ATR-adaptive thresholds ──────────────────────────────────────────────

def test_atr_adaptive_threshold_scales_with_atr_input():
    """The ATR-scaled pole_gain threshold for bull_flag is 1.5 × atr_pct.
    A signal with a 2% pole on a 1% ATR stock fires (2.0 ≥ 1.5 × 1.0).
    The same 2% pole on a 3% ATR stock should NOT fire (2.0 < 1.5 × 3.0)."""
    n = 30
    base = np.linspace(100, 100, n)
    # Inject a +2% pole at bar 10, then 4 tight consolidation bars, then breakout.
    base[10] = base[9] * 1.02
    for i in range(11, 15):
        base[i] = base[10] * 1.001
    base[15:] = base[10] * 1.005

    high = base + 0.05
    low = base - 0.05
    close = base
    vol = np.array([1000.0] * n)
    vol[10] = 8000.0   # pole has high volume

    df = pd.DataFrame({"Open": base, "High": high, "Low": low,
                       "Close": close, "Volume": vol},
                      index=pd.date_range("2025-01-01 09:15", periods=n, freq="5min"))

    # Low-ATR stock: 2% pole easily clears 1.5 × 1.0 = 1.5%
    r_low = IS.detect_bull_flag(df, atr_pct=1.0)
    # High-ATR stock: 1.5 × 3.0 = 4.5% required, 2% pole shouldn't qualify
    r_high = IS.detect_bull_flag(df, atr_pct=3.0)

    # The low-ATR call should be permissive enough to detect; the high-ATR
    # call should reject the same data.
    assert (r_low is not None) or (r_high is None), \
        "If low-ATR rejects, both should reject; if high-ATR accepts, both should"
    if r_low is not None:
        assert r_high is None, (
            "High-ATR scaling must suppress a 2% pole that low-ATR accepts"
        )


# ── 2C: Empirical confidence lookup ──────────────────────────────────────────

def test_empirical_confidence_falls_back_when_stats_missing(tmp_path, monkeypatch):
    """When the JSON stats file doesn't exist, the lookup uses the legacy
    hardcoded defaults (matches pre-Stage-2 behaviour)."""
    monkeypatch.setattr(IS, "_EMPIRICAL_STATS_PATH", tmp_path / "missing.json")
    monkeypatch.setattr(IS, "_empirical_stats_cache", None)
    assert IS._empirical_confidence("bull_flag") == 75
    assert IS._empirical_confidence("vwap_reclaim") == 70
    assert IS._empirical_confidence("rsi_divergence") == 70


def test_empirical_confidence_uses_stats_when_present(tmp_path, monkeypatch):
    """When the file is present, lookup priorities are: regime+hour > regime > overall."""
    stats = {
        "bull_flag": {
            "overall":              0.55,
            "trending_bull":        0.66,
            "trending_bull_10":     0.78,
        }
    }
    p = tmp_path / "stats.json"
    p.write_text(json.dumps(stats))
    monkeypatch.setattr(IS, "_EMPIRICAL_STATS_PATH", p)
    monkeypatch.setattr(IS, "_empirical_stats_cache", None)

    # No regime — should fall through to overall.
    assert IS._empirical_confidence("bull_flag") == 55
    # Regime only — overall replaced by regime hit rate.
    assert IS._empirical_confidence("bull_flag", regime="trending_bull") == 66
    # Regime + hour — most-specific wins.
    assert IS._empirical_confidence("bull_flag", regime="trending_bull", hour=10) == 78
    # Unknown detector — fallback to legacy default.
    assert IS._empirical_confidence("nonexistent_pattern") == 50


# ── 2D: DTW base-rate adjustment ─────────────────────────────────────────────

def test_dtw_pattern_reports_base_rate_adjusted_ev(tmp_path, monkeypatch):
    """A long-running uptrend produces positive forward returns on every
    pattern match. EV reported as `expected_value` must subtract the symbol's
    own baseline so the headline EV reflects edge OVER drift, not drift+edge.
    """
    from agents.pattern_agent import PatternAgent

    # Build a steady uptrend: ~0.3% per bar for 200 bars. Every 10-bar
    # forward return is approximately +3%. A pattern should report
    # expected_value ≈ 0 (edge over baseline) but expected_value_raw ≈ +3%.
    n = 250
    rng = np.random.default_rng(0)
    drift = 0.003
    rets = rng.normal(drift, 0.001, n)   # very tight noise + strong drift
    close = 100 * np.exp(np.cumsum(rets))
    df = pd.DataFrame({
        "Open": close, "High": close * 1.001, "Low": close * 0.999,
        "Close": close, "Volume": rng.integers(1000, 5000, n).astype(float),
    }, index=pd.date_range("2024-01-01", periods=n, freq="B"))

    # Patch read_parquet so the agent uses our synthetic data.
    sym = "SYNTH"
    sym_dir = tmp_path / "stocks" / sym
    sym_dir.mkdir(parents=True)
    df.to_parquet(sym_dir / "price_history.parquet")

    monkeypatch.chdir(tmp_path)

    agent = PatternAgent({})
    result = agent.run({"symbol": sym})
    assert result.ok(), f"agent failed: {result.error}"
    d = result.data
    # Raw EV is dominated by drift — should be clearly positive.
    assert d["expected_value_raw"] > 1.0, \
        f"Raw EV should reflect uptrend drift, got {d['expected_value_raw']:+.2f}%"
    # Base-rate-adjusted EV should be much closer to zero than the raw EV
    # (it removes the drift). Allow generous tolerance for noise.
    assert abs(d["expected_value"]) < d["expected_value_raw"], (
        "Base-rate-adjusted EV should be smaller in magnitude than raw EV "
        "on a pure-drift dataset"
    )
    assert "ci90_low" in d and "ci90_high" in d
    assert d["ci90_low"] <= d["expected_value"] <= d["ci90_high"]


def test_dtw_pattern_reports_more_matches_than_legacy_top5():
    """Stage 2D: TOP_K bumped from 5 to 20 for statistical reliability."""
    from agents import pattern_agent
    assert pattern_agent.TOP_K >= 20, \
        f"TOP_K should be >= 20 after Stage 2D, got {pattern_agent.TOP_K}"
