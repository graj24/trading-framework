"""Tests for core.nse_historical — NSE direct OHLCV fetcher."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from unittest.mock import patch

import pandas as pd
import pytest

from core import nse_historical as nh


def _fake_jugaad_df() -> pd.DataFrame:
    """Mimic the shape jugaad-data returns."""
    return pd.DataFrame({
        "DATE": pd.to_datetime(["2026-05-13", "2026-05-14"]),
        "SERIES": ["EQ", "EQ"],
        "OPEN": [1365.2, 1356.8],
        "HIGH": [1378.0, 1364.8],
        "LOW": [1358.4, 1329.2],
        "PREV. CLOSE": [1361.8, 1361.8],
        "LTP": [1361.8, 1336.4],
        "CLOSE": [1361.8, 1336.4],
        "VOLUME": [17303059, 19976192],
    })


def test_fetch_history_returns_yfinance_schema():
    fake = _fake_jugaad_df()
    with patch("jugaad_data.nse.stock_df", return_value=fake):
        df = nh.fetch_history(
            "RELIANCE",
            start=datetime(2026, 5, 13),
            end=datetime(2026, 5, 14),
        )
    assert not df.empty
    assert isinstance(df.index, pd.DatetimeIndex)
    assert list(df.columns) == ["Open", "High", "Low", "Close", "Volume", "Dividends", "Stock Splits"]
    assert len(df) == 2
    assert df["Dividends"].sum() == 0
    assert df["Stock Splits"].sum() == 0


def test_fetch_history_handles_empty_response():
    with patch("jugaad_data.nse.stock_df", return_value=pd.DataFrame()):
        df = nh.fetch_history("RELIANCE", years=1)
    assert df.empty


def test_fetch_history_handles_none_response():
    with patch("jugaad_data.nse.stock_df", return_value=None):
        df = nh.fetch_history("RELIANCE", years=1)
    assert df.empty


def test_fetch_history_handles_exception():
    with patch("jugaad_data.nse.stock_df", side_effect=RuntimeError("NSE down")):
        df = nh.fetch_history("RELIANCE", years=1)
    assert df.empty


def test_fetch_history_passes_correct_dates():
    fake = _fake_jugaad_df()
    captured = {}

    def fake_fetch(symbol, from_date, to_date, series):
        captured["symbol"] = symbol
        captured["from_date"] = from_date
        captured["to_date"] = to_date
        captured["series"] = series
        return fake

    with patch("jugaad_data.nse.stock_df", side_effect=fake_fetch):
        nh.fetch_history(
            "TCS",
            start=datetime(2025, 1, 1),
            end=datetime(2025, 6, 30),
        )

    assert captured["symbol"] == "TCS"
    assert captured["from_date"] == date(2025, 1, 1)
    assert captured["to_date"] == date(2025, 6, 30)
    assert captured["series"] == "EQ"


def test_fetch_history_uppercases_symbol():
    fake = _fake_jugaad_df()
    captured = {}

    def fake_fetch(symbol, **kw):
        captured["symbol"] = symbol
        return fake

    with patch("jugaad_data.nse.stock_df", side_effect=fake_fetch):
        nh.fetch_history("reliance", years=1)

    assert captured["symbol"] == "RELIANCE"


def test_fetch_history_drops_missing_required_columns():
    """If essential OHLC fields are missing/NaN, those rows are dropped."""
    bad = pd.DataFrame({
        "DATE": pd.to_datetime(["2026-05-13", "2026-05-14"]),
        "OPEN": [100.0, None],
        "HIGH": [105.0, 110.0],
        "LOW": [99.0, 108.0],
        "CLOSE": [102.0, None],
        "VOLUME": [1000, 2000],
    })
    with patch("jugaad_data.nse.stock_df", return_value=bad):
        df = nh.fetch_history("X", years=1)
    assert len(df) == 1
    assert df.iloc[0]["Open"] == 100.0


def test_to_datetime_accepts_date():
    assert nh._to_datetime(date(2025, 5, 1)) == datetime(2025, 5, 1)


def test_to_datetime_passes_through_datetime():
    dt = datetime(2025, 5, 1, 9, 30)
    assert nh._to_datetime(dt) is dt
