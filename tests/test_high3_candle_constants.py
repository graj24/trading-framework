"""Regression test for HIGH-3 — undefined CANDLE_LOOKBACK / CANDLE_INTERVAL."""
from __future__ import annotations

import importlib


def test_intraday_scanner_defines_candle_constants():
    """Both constants must exist as module-level attributes (the function
    `get_intraday_candles` references them at call time)."""
    mod = importlib.import_module("agents.intraday_scanner")
    assert hasattr(mod, "CANDLE_LOOKBACK"), "CANDLE_LOOKBACK missing"
    assert hasattr(mod, "CANDLE_INTERVAL"), "CANDLE_INTERVAL missing"
    # Sanity: yfinance period / interval strings.
    assert isinstance(mod.CANDLE_LOOKBACK, str) and mod.CANDLE_LOOKBACK
    assert isinstance(mod.CANDLE_INTERVAL, str) and mod.CANDLE_INTERVAL


def test_get_intraday_candles_does_not_raise_nameerror_internally(monkeypatch):
    """Even when yfinance fails (no network in CI), the function must NOT
    raise NameError. Currently it does — the bare except hides it as None."""
    from agents import intraday_scanner

    # Stub yf.Ticker so we don't touch the network.
    class _StubTicker:
        def __init__(self, *_a, **_kw): pass
        def history(self, **_kw):
            import pandas as pd
            return pd.DataFrame()

    monkeypatch.setattr(intraday_scanner.yf, "Ticker", _StubTicker)

    # Should return None (empty df), not raise.
    result = intraday_scanner.get_intraday_candles("RELIANCE")
    assert result is None
