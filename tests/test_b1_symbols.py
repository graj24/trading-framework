"""Tests for B.1 — `core/symbols.py`."""
from __future__ import annotations

import pytest


def test_nifty_50_size():
    from core.symbols import NIFTY_50
    # Real NIFTY 50 has 50 names; we ship a slightly extended list to cover
    # recent membership shuffles. Just make sure it's not crazy.
    assert 45 <= len(NIFTY_50) <= 60


def test_normalisation_helpers():
    from core.symbols import (
        to_nse, to_fs_safe, to_yfinance_ticker, to_groww_ticker, is_nifty_50,
    )
    assert to_nse("BAJAJ_AUTO") == "BAJAJ-AUTO"
    assert to_nse("BAJAJ-AUTO") == "BAJAJ-AUTO"
    assert to_nse("reliance") == "RELIANCE"
    assert to_fs_safe("BAJAJ-AUTO") == "BAJAJ_AUTO"
    assert to_fs_safe("BAJAJ_AUTO") == "BAJAJ_AUTO"
    assert to_yfinance_ticker("RELIANCE") == "RELIANCE.NS"
    assert to_yfinance_ticker("BAJAJ_AUTO") == "BAJAJ-AUTO.NS"
    assert to_groww_ticker("RELIANCE") == "RELIANCE"
    assert is_nifty_50("RELIANCE")
    assert is_nifty_50("bajaj_auto")
    assert not is_nifty_50("FAKESYMBOL")
