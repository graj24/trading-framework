"""Test for C.1 — `load_market_data` caches to parquet."""
from __future__ import annotations

import os
from pathlib import Path

import pandas as pd
import pytest


def test_market_data_cache_hit_avoids_yfinance(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    import ml_model

    # First call: stub yfinance to return a known series, write the cache.
    fetch_calls = {"n": 0}

    class _FakeTicker:
        def __init__(self, name): self.name = name
        def history(self, start=None, end=None, interval=None):
            fetch_calls["n"] += 1
            idx = pd.date_range(start, end, freq="B")
            return pd.DataFrame(
                {"Close": [100.0 + i for i in range(len(idx))]},
                index=idx,
            )

    monkeypatch.setattr(ml_model.yf, "Ticker", _FakeTicker)

    out1 = ml_model.load_market_data("2024-01-01", "2024-01-31")
    assert out1, "first call returned empty"
    first_calls = fetch_calls["n"]
    assert first_calls > 0

    # Cache should now exist.
    assert (tmp_path / "stocks" / "_market_data.parquet").exists()
    assert (tmp_path / "stocks" / "_market_data.meta").exists()

    # Second call within the cached range — must NOT hit yfinance again.
    out2 = ml_model.load_market_data("2024-01-05", "2024-01-20")
    assert out2, "second call returned empty"
    assert fetch_calls["n"] == first_calls, "cache miss when it should have hit"


def test_cache_refetches_when_range_extends(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    import ml_model

    fetch_calls = {"n": 0}

    class _FakeTicker:
        def __init__(self, name): self.name = name
        def history(self, start=None, end=None, interval=None):
            fetch_calls["n"] += 1
            idx = pd.date_range(start, end, freq="B")
            return pd.DataFrame(
                {"Close": [100.0] * len(idx)},
                index=idx,
            )

    monkeypatch.setattr(ml_model.yf, "Ticker", _FakeTicker)

    ml_model.load_market_data("2024-01-01", "2024-01-31")
    after_first = fetch_calls["n"]

    # Request extends beyond the cached end → must refetch.
    ml_model.load_market_data("2024-01-01", "2024-06-30")
    assert fetch_calls["n"] > after_first, "should have refetched on range extension"
