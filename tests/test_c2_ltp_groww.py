"""Tests for C.2 — `_get_ltp` prefers Groww, falls back to yfinance."""
from __future__ import annotations

import pandas as pd
import pytest


def test_get_ltp_uses_groww_when_available(monkeypatch):
    """When Groww returns a price, yfinance is NOT called."""
    from agents import execution_agent

    class _StubGroww:
        def get_ltp(self, syms):
            return {syms[0]: 1234.5}

    monkeypatch.setattr(
        "core.groww_client.get_groww_client", lambda: _StubGroww()
    )

    yf_calls = {"n": 0}
    class _StubTicker:
        def __init__(self, _ticker): pass
        def history(self, period=None):
            yf_calls["n"] += 1
            return pd.DataFrame({"Close": [999.9]}, index=pd.date_range("2024-01-01", periods=1))

    monkeypatch.setattr(execution_agent.yf, "Ticker", _StubTicker)

    assert execution_agent._get_ltp("RELIANCE") == 1234.5
    assert yf_calls["n"] == 0, "yfinance should not have been called"


def test_get_ltp_falls_back_to_yfinance(monkeypatch):
    """When Groww raises or returns empty, yfinance fills in."""
    from agents import execution_agent

    class _StubGroww:
        def get_ltp(self, syms):
            return {}  # empty result

    monkeypatch.setattr(
        "core.groww_client.get_groww_client", lambda: _StubGroww()
    )

    class _StubTicker:
        def __init__(self, _ticker): pass
        def history(self, period=None):
            return pd.DataFrame({"Close": [987.6]}, index=pd.date_range("2024-01-01", periods=1))

    monkeypatch.setattr(execution_agent.yf, "Ticker", _StubTicker)
    assert execution_agent._get_ltp("RELIANCE") == 987.6


def test_get_ltp_returns_zero_when_both_fail(monkeypatch):
    from agents import execution_agent

    def _raise():
        raise RuntimeError("groww down")

    monkeypatch.setattr("core.groww_client.get_groww_client", _raise)

    class _StubTicker:
        def __init__(self, _ticker): pass
        def history(self, period=None):
            raise RuntimeError("yfinance down")

    monkeypatch.setattr(execution_agent.yf, "Ticker", _StubTicker)
    assert execution_agent._get_ltp("RELIANCE") == 0.0
