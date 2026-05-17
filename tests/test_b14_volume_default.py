"""Test for B14 — `master.scores["volume_ratio"]` shouldn't silently default
to 1.0 (which lets the volume filter pass when TechnicalAgent failed)."""
from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def _stub_heavy_imports(monkeypatch):
    fake_pipe = MagicMock(return_value=[[{"label": "POSITIVE", "score": 0.5}]])
    fake_transformers = types.ModuleType("transformers")
    fake_transformers.pipeline = lambda *a, **kw: fake_pipe  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "transformers", fake_transformers)


def test_when_technical_agent_fails_buy_does_not_pass_volume_filter(monkeypatch):
    """If TechnicalAgent errors, the master pipeline must NOT silently treat
    a missing volume_ratio as 'volume is fine, BUY allowed'.

    This is the heart of B14: a failed indicator should not look the same as
    a healthy one.
    """
    from agents.base import AgentResult, AgentStatus
    from agents.master import MasterAgent

    class _StubFailingTech:
        """TechnicalAgent that errors out."""
        def run(self, _ctx=None):
            return AgentResult("TechnicalAgent", AgentStatus.ERROR, error="stub")

    class _StubGoodAgent:
        def run(self, _ctx=None):
            return AgentResult(
                "stub", AgentStatus.DONE,
                data={
                    "current_price": 100.0,
                    "trend": "up", "macd_signal": "bullish",
                    "technical_score": 8, "sentiment": 0.4,
                    "pattern_ev": 1.0, "win_rate": 60,
                    "regime": "trending_bull", "rsi": 55,
                    "expected_value": 1.0, "avg_win": 2.0, "avg_loss": -1.0,
                },
            )

    config = {
        "trading": {"capital": 10_000, "mode": "paper"},
        "llm": {"model": "x"},
        "watchlist": [],
    }
    master = MasterAgent(config)
    master.technical_agent = _StubFailingTech()
    master.news_agent = _StubGoodAgent()
    master.pattern_agent = _StubGoodAgent()
    master.regime_agent = _StubGoodAgent()

    # Force LLM to say BUY so we can verify the filter still rejects.
    monkeypatch.setattr(
        "agents.master._llm_decision",
        lambda *a, **kw: {"decision": "BUY", "confidence": 80, "entry": 100,
                          "stop_loss": 99, "target": 103, "reasoning": "stub"},
    )

    result = master.run_for_stock("RELIANCE")
    assert result.ok()
    # The decision MUST be downgraded from BUY because volume_ratio is unknown.
    assert result.data["decision"] in ("HOLD", "SKIP"), \
        f"Expected HOLD/SKIP when TechnicalAgent failed, got {result.data['decision']}"
    # And the reasoning should mention the missing volume signal.
    reasoning = result.data["reasoning"].lower()
    assert "vol" in reasoning or "volume" in reasoning or "filters" in reasoning, \
        f"Expected reasoning to explain the volume filter rejection: {reasoning!r}"


def test_when_only_volume_ratio_missing_buy_is_blocked(monkeypatch):
    """The narrower failure mode: TechAgent returned trend / macd but the
    volume calculation failed. Today the code defaults volume_ratio to 1.0
    (passes filter) — B14 says this should fail closed."""
    from agents.base import AgentResult, AgentStatus
    from agents.master import MasterAgent

    class _StubPartialTech:
        """TechnicalAgent that returns trend / macd but no volume_ratio."""
        def run(self, _ctx=None):
            return AgentResult(
                "TechnicalAgent", AgentStatus.DONE,
                data={
                    "current_price": 100.0,
                    "trend": "up",
                    "macd_signal": "bullish",
                    # NOTE: volume_ratio is intentionally absent here.
                    "technical_score": 8,
                    "rsi": 55,
                },
            )

    class _StubGoodAgent:
        def run(self, _ctx=None):
            return AgentResult(
                "stub", AgentStatus.DONE,
                data={"sentiment": 0.4, "pattern_ev": 1.0, "win_rate": 60,
                      "regime": "trending_bull", "expected_value": 1.0,
                      "avg_win": 2.0, "avg_loss": -1.0},
            )

    config = {
        "trading": {"capital": 10_000, "mode": "paper"},
        "llm": {"model": "x"},
        "watchlist": [],
    }
    master = MasterAgent(config)
    master.technical_agent = _StubPartialTech()
    master.news_agent = _StubGoodAgent()
    master.pattern_agent = _StubGoodAgent()
    master.regime_agent = _StubGoodAgent()

    monkeypatch.setattr(
        "agents.master._llm_decision",
        lambda *a, **kw: {"decision": "BUY", "confidence": 80, "entry": 100,
                          "stop_loss": 99, "target": 103, "reasoning": "stub"},
    )

    result = master.run_for_stock("RELIANCE")
    assert result.ok()
    assert result.data["decision"] in ("HOLD", "SKIP"), \
        f"Expected HOLD/SKIP when volume_ratio missing, got {result.data['decision']}: {result.data['reasoning']!r}"
