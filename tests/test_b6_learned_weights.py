"""Test for B.6 / B3 — learned signal weights influence the rule fallback."""
from __future__ import annotations

import json
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def _stub_heavy_imports(monkeypatch):
    fake_pipe = MagicMock(return_value=[[{"label": "POSITIVE", "score": 0.5}]])
    fake_transformers = types.ModuleType("transformers")
    fake_transformers.pipeline = lambda *a, **kw: fake_pipe  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "transformers", fake_transformers)


def _seed_kb(stocks_root: Path, symbol: str, weights: dict, monkeypatch) -> None:
    sym_dir = stocks_root / symbol
    sym_dir.mkdir(parents=True, exist_ok=True)
    (sym_dir / "signal_weights.json").write_text(json.dumps(weights))
    # The KB module reads from a module-level STOCKS_DIR — point it here.
    monkeypatch.setattr("core.knowledge_base.STOCKS_DIR", stocks_root)


def _scores(**overrides):
    base = {
        "technical_score": 7,
        "sentiment": 0.0,
        "pattern_ev": 0.0,
        "win_rate": 50,
        "regime": "trending_bull",
        "tier": None,
        "trend": "up",
        "macd_signal": "bullish",
        "volume_ratio": 1.5,
    }
    base.update(overrides)
    return base


def test_high_learned_weights_lift_composite(tmp_path, monkeypatch):
    """A stock whose technical_score weight is 2.0 should produce a higher
    composite than one whose weight is 0.5, holding raw scores constant."""
    monkeypatch.chdir(tmp_path)
    from agents.master import _rule_based_decision

    _seed_kb(tmp_path / "stocks", "HIGH", {"technical_score": 2.0,
                                           "news_sentiment": 1.0,
                                           "pattern_ev":     1.0}, monkeypatch)
    _seed_kb(tmp_path / "stocks", "LOW",  {"technical_score": 0.5,
                                           "news_sentiment": 1.0,
                                           "pattern_ev":     1.0}, monkeypatch)

    out_high = _rule_based_decision(100.0, _scores(), symbol="HIGH")
    out_low  = _rule_based_decision(100.0, _scores(), symbol="LOW")
    assert out_high["confidence"] > out_low["confidence"], (
        f"learned weights ignored: HIGH={out_high['confidence']} LOW={out_low['confidence']}"
    )


def test_no_symbol_falls_back_to_regime_only_weights(tmp_path, monkeypatch):
    """Calling without `symbol` is the legacy behaviour — must not crash."""
    monkeypatch.chdir(tmp_path)
    from agents.master import _rule_based_decision
    out = _rule_based_decision(100.0, _scores())
    assert "decision" in out


def test_clip_caps_runaway_weights(tmp_path, monkeypatch):
    """Weights of 100x or 0 must NOT produce extreme composites — they're
    clipped to [0.5, 2.0] before multiplying."""
    monkeypatch.chdir(tmp_path)
    from agents.master import _rule_based_decision
    _seed_kb(tmp_path / "stocks", "RUNAWAY", {"technical_score": 100.0,
                                              "news_sentiment": 0.0,
                                              "pattern_ev":     0.0}, monkeypatch)
    out = _rule_based_decision(100.0, _scores(), symbol="RUNAWAY")
    # Should still be a sensible 0-100 confidence.
    assert 0 <= out["confidence"] <= 95
