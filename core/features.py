"""
Canonical technical indicators.

Single source of truth for RSI/MACD/EMA/ATR/ADX/Bollinger/OBV/Stoch/VWAP.

Before this module existed, RSI was implemented in five places:
  - models/ml_model.py
  - models/india_intraday_model.py
  - agents/technical_agent.py
  - agents/intraday_scanner.py (used SMA — different formula!)
  - scripts/simulate_day.py

Each was slightly different. This module fixes that. Every implementation
is the well-known industry-standard form (Wilder's smoothing for RSI/ATR/ADX,
exponential MACD, etc.).

All functions take and return pandas Series unless documented otherwise.
"""
from __future__ import annotations

from typing import Tuple

import numpy as np
import pandas as pd


# ── Trend ────────────────────────────────────────────────────────────────────

def ema(s: pd.Series, span: int) -> pd.Series:
    """Exponential moving average."""
    return s.ewm(span=span, adjust=False).mean()


def sma(s: pd.Series, period: int) -> pd.Series:
    """Simple moving average."""
    return s.rolling(period).mean()


# ── Momentum ─────────────────────────────────────────────────────────────────

def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Relative Strength Index using Wilder's smoothing (EMA with alpha=1/period).

    This is the standard RSI as defined by J. Welles Wilder Jr. (1978). All
    legacy implementations except `intraday_scanner` used this form.
    """
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, min_periods=period).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, min_periods=period).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def macd_line(close: pd.Series, fast: int = 12, slow: int = 26) -> pd.Series:
    """MACD line: fast EMA − slow EMA."""
    return ema(close, fast) - ema(close, slow)


def macd_signal(line: pd.Series, signal: int = 9) -> pd.Series:
    """MACD signal line: EMA of MACD line."""
    return ema(line, signal)


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9
         ) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """Returns (macd_line, signal_line, histogram)."""
    line = macd_line(close, fast, slow)
    sig = macd_signal(line, signal)
    return line, sig, line - sig


def macd_hist(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9
              ) -> pd.Series:
    """Convenience: just the MACD histogram."""
    line = macd_line(close, fast, slow)
    return line - macd_signal(line, signal)


def stoch_k(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14
            ) -> pd.Series:
    """Stochastic %K."""
    lo = low.rolling(period).min()
    hi = high.rolling(period).max()
    return (close - lo) / (hi - lo + 1e-9) * 100


def stoch_d(k: pd.Series, period: int = 3) -> pd.Series:
    """Stochastic %D — SMA of %K."""
    return k.rolling(period).mean()


def roc(close: pd.Series, period: int) -> pd.Series:
    """Rate of change in percent: (close / close[-period] - 1) * 100."""
    return (close / close.shift(period) - 1) * 100


# ── Volatility ───────────────────────────────────────────────────────────────

def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    """True Range: max(H-L, |H-PrevClose|, |L-PrevClose|)."""
    return pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs(),
    ], axis=1).max(axis=1)


def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14
        ) -> pd.Series:
    """Average True Range (Wilder's smoothing)."""
    return true_range(high, low, close).ewm(alpha=1 / period, min_periods=period).mean()


def hist_vol(close: pd.Series, period: int = 20, periods_per_year: int = 252
             ) -> pd.Series:
    """Annualised historical volatility from log returns over a rolling window."""
    return close.pct_change().rolling(period).std() * np.sqrt(periods_per_year)


def adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14
        ) -> pd.Series:
    """Average Directional Index (Wilder's). Returns the ADX series."""
    plus_dm_raw = high.diff().clip(lower=0)
    minus_dm_raw = (-low.diff()).clip(lower=0)
    # When both directional moves are present, only the larger counts.
    plus_dm = plus_dm_raw.where(plus_dm_raw > minus_dm_raw, 0.0)
    minus_dm = minus_dm_raw.where(minus_dm_raw > plus_dm_raw, 0.0)

    atr_ = atr(high, low, close, period)
    plus_di = 100 * (plus_dm.ewm(alpha=1 / period, min_periods=period).mean() / atr_)
    minus_di = 100 * (minus_dm.ewm(alpha=1 / period, min_periods=period).mean() / atr_)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(alpha=1 / period, min_periods=period).mean()


def adx_value(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14
              ) -> float:
    """Convenience: latest ADX value as float (0.0 if undefined)."""
    s = adx(high, low, close, period)
    return float(s.iloc[-1]) if not s.empty and not pd.isna(s.iloc[-1]) else 0.0


# ── Bollinger Bands ──────────────────────────────────────────────────────────

def bollinger(close: pd.Series, period: int = 20, n_std: float = 2.0
              ) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """Returns (upper, mid, lower)."""
    mid = close.rolling(period).mean()
    std = close.rolling(period).std()
    return mid + n_std * std, mid, mid - n_std * std


def bb_position(close: pd.Series, period: int = 20, n_std: float = 2.0
                ) -> pd.Series:
    """Where price sits inside the bands: 0 = lower band, 1 = upper band."""
    mid = close.rolling(period).mean()
    std = close.rolling(period).std()
    width = 2 * n_std * std
    return (close - (mid - n_std * std)) / (width + 1e-9)


def bb_width(close: pd.Series, period: int = 20, n_std: float = 2.0
             ) -> pd.Series:
    """Band width relative to mid: 2*n_std*std / mid."""
    mid = close.rolling(period).mean()
    std = close.rolling(period).std()
    return (2 * n_std * std) / (mid + 1e-9)


# ── Volume ───────────────────────────────────────────────────────────────────

def obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    """On-Balance Volume."""
    return (np.sign(close.diff()).fillna(0) * volume).cumsum()


def vwap(high: pd.Series, low: pd.Series, close: pd.Series, volume: pd.Series
         ) -> float:
    """Volume-Weighted Average Price over the input window (scalar).

    Pass `.tail(N)` of each series to compute the rolling N-bar VWAP.
    """
    typical = (high + low + close) / 3
    denom = volume.sum()
    return float((typical * volume).sum() / denom) if denom else float("nan")


def vwap_series(high: pd.Series, low: pd.Series, close: pd.Series,
                volume: pd.Series) -> pd.Series:
    """Cumulative VWAP at each bar (anchored at the start of the input)."""
    typical = (high + low + close) / 3
    cum_pv = (typical * volume).cumsum()
    cum_v = volume.cumsum()
    return cum_pv / cum_v.replace(0, np.nan)


def volume_ratio(volume: pd.Series, period: int = 20) -> pd.Series:
    """Volume relative to its rolling mean."""
    return volume / (volume.rolling(period).mean() + 1e-9)
