"""
Equivalence tests for core/features.py against the legacy local
implementations that previously lived inside ml_model, india_intraday_model,
technical_agent, regime_agent and intraday_scanner.

These tests exist so that the indicator consolidation (Stage 0) lands without
silently changing the values any consumer sees. If an existing model's output
moves after this refactor, that is a real bug worth catching; these tests
make such a move loud.

Note: `intraday_scanner.detect_rsi_divergence` previously used SMA-based RSI,
which is *not* the standard Wilder formula used everywhere else. We
intentionally migrate that caller to Wilder; an explicit test below documents
the difference.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from core import features as F


# ── Synthetic OHLCV ──────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def ohlcv() -> pd.DataFrame:
    rng = np.random.default_rng(42)
    n = 300
    rets = rng.normal(0, 0.012, n)
    close = pd.Series(100 * np.exp(np.cumsum(rets)))
    high = close * (1 + rng.uniform(0, 0.01, n))
    low = close * (1 - rng.uniform(0, 0.01, n))
    vol = pd.Series(rng.integers(1_000, 10_000, n).astype(float))
    return pd.DataFrame({"Open": close, "High": high, "Low": low,
                         "Close": close, "Volume": vol})


# ── Reference implementations (verbatim copies of what was in the codebase) ──

def _legacy_ema_mlmodel(s, n):
    return s.ewm(span=n, adjust=False).mean()


def _legacy_rsi_mlmodel(s, n=14):
    d = s.diff()
    g = d.clip(lower=0).ewm(alpha=1/n, min_periods=n).mean()
    l = (-d.clip(upper=0)).ewm(alpha=1/n, min_periods=n).mean()
    return 100 - 100 / (1 + g / l.replace(0, np.nan))


def _legacy_macd_hist_mlmodel(s):
    m = _legacy_ema_mlmodel(s, 12) - _legacy_ema_mlmodel(s, 26)
    return m - _legacy_ema_mlmodel(m, 9)


def _legacy_atr_mlmodel(h, l, c, n=14):
    tr = pd.concat([h-l, (h-c.shift()).abs(), (l-c.shift()).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1/n, min_periods=n).mean()


def _legacy_stoch_mlmodel(h, l, c, k=14):
    lo = l.rolling(k).min()
    hi = h.rolling(k).max()
    return (c - lo) / (hi - lo + 1e-9) * 100


def _legacy_obv_mlmodel(c, v):
    return (np.sign(c.diff()).fillna(0) * v).cumsum()


def _legacy_bb_position_mlmodel(c, n=20):
    sma = c.rolling(n).mean()
    std = c.rolling(n).std()
    return (c - (sma - 2*std)) / (4*std + 1e-9)


def _legacy_bb_width_mlmodel(c, n=20):
    sma = c.rolling(n).mean()
    std = c.rolling(n).std()
    return (4 * std) / (sma + 1e-9)


def _legacy_hist_vol_mlmodel(c, n):
    return c.pct_change().rolling(n).std() * np.sqrt(252)


def _legacy_rsi_techagent(close, period=14):
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1/period, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1/period, min_periods=period).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def _legacy_vwap_techagent(high, low, close, volume):
    typical = (high + low + close) / 3
    return (typical * volume).sum() / volume.sum()


def _legacy_compute_adx_regime(high, low, close, period=14):
    """Verbatim copy of regime_agent.compute_adx — returns float scalar."""
    plus_dm = high.diff()
    minus_dm = -low.diff()
    plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0.0)
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0.0)
    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr_ = tr.ewm(alpha=1/period, min_periods=period).mean()
    plus_di = 100 * (plus_dm.ewm(alpha=1/period, min_periods=period).mean() / atr_)
    minus_di = 100 * (minus_dm.ewm(alpha=1/period, min_periods=period).mean() / atr_)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    adx_s = dx.ewm(alpha=1/period, min_periods=period).mean()
    return float(adx_s.iloc[-1]) if not adx_s.empty else 0.0


# ── Equivalence assertions ───────────────────────────────────────────────────

def _series_close(a, b, tol=1e-9):
    """Compare two series ignoring NaN positions; both should be NaN at the
    same positions and equal elsewhere."""
    a, b = a.astype(float), b.astype(float)
    assert a.isna().equals(b.isna()), "NaN positions differ"
    mask = ~a.isna()
    np.testing.assert_allclose(a[mask].values, b[mask].values, rtol=tol, atol=tol)


def test_ema_matches_legacy(ohlcv):
    close = ohlcv["Close"]
    for span in (9, 12, 20, 26, 50, 200):
        _series_close(F.ema(close, span), _legacy_ema_mlmodel(close, span))


def test_rsi_matches_mlmodel_legacy(ohlcv):
    _series_close(F.rsi(ohlcv["Close"]), _legacy_rsi_mlmodel(ohlcv["Close"]))


def test_rsi_matches_techagent_legacy_in_well_defined_zones(ohlcv):
    """tech_agent's RSI doesn't divide-by-zero-guard, so it differs only on
    bars where loss==0. Excluding those bars, it should be identical."""
    canonical = F.rsi(ohlcv["Close"])
    legacy = _legacy_rsi_techagent(ohlcv["Close"])
    # Both implementations agree wherever both are finite.
    finite = canonical.notna() & legacy.notna() & np.isfinite(legacy)
    np.testing.assert_allclose(canonical[finite].values, legacy[finite].values,
                                rtol=1e-9, atol=1e-9)


def test_macd_hist_matches_legacy(ohlcv):
    _series_close(F.macd_hist(ohlcv["Close"]),
                  _legacy_macd_hist_mlmodel(ohlcv["Close"]))


def test_atr_matches_legacy(ohlcv):
    _series_close(F.atr(ohlcv["High"], ohlcv["Low"], ohlcv["Close"]),
                  _legacy_atr_mlmodel(ohlcv["High"], ohlcv["Low"], ohlcv["Close"]))


def test_stoch_k_matches_legacy(ohlcv):
    _series_close(F.stoch_k(ohlcv["High"], ohlcv["Low"], ohlcv["Close"]),
                  _legacy_stoch_mlmodel(ohlcv["High"], ohlcv["Low"], ohlcv["Close"]))


def test_obv_matches_legacy(ohlcv):
    _series_close(F.obv(ohlcv["Close"], ohlcv["Volume"]),
                  _legacy_obv_mlmodel(ohlcv["Close"], ohlcv["Volume"]))


def test_bb_position_matches_legacy(ohlcv):
    _series_close(F.bb_position(ohlcv["Close"]),
                  _legacy_bb_position_mlmodel(ohlcv["Close"]))


def test_bb_width_matches_legacy(ohlcv):
    _series_close(F.bb_width(ohlcv["Close"]),
                  _legacy_bb_width_mlmodel(ohlcv["Close"]))


def test_hist_vol_matches_legacy(ohlcv):
    _series_close(F.hist_vol(ohlcv["Close"], 20),
                  _legacy_hist_vol_mlmodel(ohlcv["Close"], 20))


def test_vwap_scalar_matches_legacy(ohlcv):
    canonical = F.vwap(ohlcv["High"].tail(20), ohlcv["Low"].tail(20),
                       ohlcv["Close"].tail(20), ohlcv["Volume"].tail(20))
    legacy = _legacy_vwap_techagent(ohlcv["High"].tail(20), ohlcv["Low"].tail(20),
                                     ohlcv["Close"].tail(20), ohlcv["Volume"].tail(20))
    np.testing.assert_allclose(canonical, legacy, rtol=1e-9, atol=1e-9)


def test_adx_value_matches_regime_legacy(ohlcv):
    """`regime_agent.compute_adx` returns a float; `F.adx_value` returns the
    same scalar so it can be a drop-in replacement."""
    canonical = F.adx_value(ohlcv["High"], ohlcv["Low"], ohlcv["Close"])
    legacy = _legacy_compute_adx_regime(ohlcv["High"], ohlcv["Low"], ohlcv["Close"])
    np.testing.assert_allclose(canonical, legacy, rtol=1e-9, atol=1e-9)


def test_intraday_scanner_rsi_was_different_documentation():
    """Documents the deliberate change.

    intraday_scanner.detect_rsi_divergence used SMA-based RSI:
        gain = delta.clip(lower=0).rolling(14).mean()
        loss = (-delta.clip(upper=0)).rolling(14).mean()

    Wilder's RSI (the canonical and industry-standard form) uses EWM:
        gain = delta.clip(lower=0).ewm(alpha=1/14).mean()

    They produce different values, especially in the first ~30 bars after a
    trend change. Migrating the scanner to Wilder is a deliberate behaviour
    change — there's no test that the scanner's pre-migration RSI was
    'right'; it was just inconsistent with the rest of the codebase.
    """
    rng = np.random.default_rng(0)
    close = pd.Series(100 + rng.normal(0, 1, 100).cumsum())

    delta = close.diff()
    gain_sma = delta.clip(lower=0).rolling(14).mean()
    loss_sma = (-delta.clip(upper=0)).rolling(14).mean()
    rs_sma = gain_sma / loss_sma.replace(0, np.nan)
    sma_rsi = 100 - 100 / (1 + rs_sma)

    wilder_rsi = F.rsi(close)

    # They are similar but not equal — by construction.
    diff = (sma_rsi - wilder_rsi).abs().dropna()
    assert diff.max() > 0.5, "Expected the two RSIs to diverge meaningfully"
