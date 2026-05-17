"""B-1: _gap_signal must apply the MACD filter (histogram > 0).

Two synthetic DataFrames that both pass gap/volume/EMA50 filters:
- macd_bearish: MACD histogram < 0  → should return None
- macd_bullish: MACD histogram > 0  → should return a trade dict
"""
import numpy as np
import pandas as pd
import pytest

from core.replay import _gap_signal


def _make_df(macd_bull: bool) -> pd.DataFrame:
    """50 rows of price data engineered so:
    - gap_pct >= 2% on last row
    - Volume[-1] >= vol_avg20 * 1.5
    - prev_row Close >= EMA50
    - MACD histogram is positive (macd_bull=True) or negative (macd_bull=False)
    """
    n = 52
    # Steadily rising closes so EMA50 stays well below price
    closes = np.linspace(100, 120, n)
    # Last row gaps up
    opens  = np.concatenate([closes[:-1], [closes[-2] * 1.025]])  # 2.5% gap
    highs  = opens * 1.03
    lows   = opens * 0.98
    # Volume: last row is 2× average of prior 20
    volumes = np.ones(n) * 1000
    volumes[-1] = 3000  # well above 1.5× avg

    df = pd.DataFrame({
        "Open":   opens,
        "High":   highs,
        "Low":    lows,
        "Close":  closes,
        "Volume": volumes,
    }, index=pd.date_range("2024-01-01", periods=n, freq="B"))

    if not macd_bull:
        # Force MACD histogram negative on last row by making recent closes drop
        df.loc[df.index[-5:], "Close"] = closes[-6] * np.linspace(1.0, 0.97, 5)

    return df


def test_macd_bearish_returns_none():
    """When MACD histogram <= 0, _gap_signal must return None."""
    df = _make_df(macd_bull=False)
    assert _gap_signal(df) is None


def test_macd_bullish_returns_trade():
    """When MACD histogram > 0 and other filters pass, _gap_signal returns a trade."""
    df = _make_df(macd_bull=True)
    result = _gap_signal(df)
    assert result is not None
    assert result["signal"] == "gap"
