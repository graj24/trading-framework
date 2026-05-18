"""
Stage 1B: prove that the intraday-model `vol_ratio_hour` feature no longer
leaks future information.

Test design — the strongest possible check for time-series leakage:

  1. Build feature dataframe F1 on the FULL dataset.
  2. Build feature dataframe F2 on the dataset truncated to the first N rows.
  3. For every row i < N, F1[i] must equal F2[i].

If the feature uses any future information (relative to row i), F1[i] would
differ from F2[i] because F1 had access to bars i+1, i+2, … when computing
its mean for hour-of-day, while F2 didn't. A bug-for-bug `transform("mean")`
implementation FAILS this test (we keep a regression fixture that confirms
the legacy form fails); the corrected expanding form PASSES it.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from models.india_intraday_model import build_features


def _synthesize_intraday_ohlcv(n_days: int = 30, bars_per_day: int = 7) -> pd.DataFrame:
    """Build synthetic 1h NSE-like data: 09:00..15:00 every business day."""
    rng = np.random.default_rng(13)
    rows = []
    base_price = 1000.0
    start = pd.Timestamp("2025-01-01")
    for d in range(n_days):
        day = start + pd.Timedelta(days=d)
        if day.dayofweek >= 5:        # skip weekends
            continue
        for h in range(9, 9 + bars_per_day):
            ts = day.replace(hour=h, minute=15)
            ret = rng.normal(0, 0.005)
            base_price *= (1 + ret)
            high = base_price * (1 + abs(rng.normal(0, 0.003)))
            low  = base_price * (1 - abs(rng.normal(0, 0.003)))
            open_ = base_price * (1 + rng.normal(0, 0.001))
            close = base_price
            vol = rng.integers(50_000, 500_000)
            rows.append((ts, open_, high, low, close, vol))
    df = pd.DataFrame(rows, columns=["ts", "Open", "High", "Low", "Close", "Volume"])
    return df.set_index("ts")


@pytest.fixture(scope="module")
def synth_df():
    return _synthesize_intraday_ohlcv(n_days=60, bars_per_day=7)


@pytest.fixture(scope="module")
def empty_market(synth_df):
    """build_features takes nifty/banknifty/vix series; supply tiny series
    with a DatetimeIndex matching the synthetic data so reindex+ffill works.
    Values are constant — they don't affect the vol_ratio_hour feature, which
    is what these tests target.
    """
    return pd.Series(100.0, index=synth_df.index)


def test_vol_ratio_hour_uses_only_past_data(synth_df, empty_market):
    """Strict causality test: feature value at row i depends only on rows 0..i-1.

    If the legacy `transform("mean")` form regresses in, this test fails
    immediately — its hour_avg for row i incorporates rows i+1..N-1.
    """
    df = synth_df

    # 1. Compute features on the full dataset.
    full = build_features(df, empty_market, empty_market, empty_market)

    # 2. Re-compute on a strict prefix.
    cut = int(len(df) * 0.6)
    prefix = build_features(df.iloc[:cut], empty_market, empty_market, empty_market)

    # 3. Every row in the prefix must match the full computation exactly.
    overlap = full.index.intersection(prefix.index)
    assert len(overlap) == cut, "prefix should cover the first `cut` rows"

    a = full.loc[overlap, "vol_ratio_hour"]
    b = prefix.loc[overlap, "vol_ratio_hour"]

    # Drop NaN positions (first observation per hour is intentionally NaN→1.0).
    diff = (a - b).abs()
    finite_diff = diff.dropna()
    assert (finite_diff < 1e-9).all(), (
        f"vol_ratio_hour leaks future data: max diff={finite_diff.max()}, "
        f"first leak at index {finite_diff.idxmax()}"
    )


def test_legacy_transform_mean_form_does_leak(synth_df):
    """Regression fixture: confirms the *legacy* implementation (which we
    deleted in Stage 1B) does fail the same causality check. This guards
    against an accidental revert."""
    df = synth_df
    idx = df.index

    # Legacy form (the buggy one we replaced).
    legacy_full   = df["Volume"] / (df.groupby(idx.hour)["Volume"].transform("mean") + 1)
    cut = int(len(df) * 0.6)
    df_pref = df.iloc[:cut]
    pref_idx = df_pref.index
    legacy_pref = df_pref["Volume"] / (df_pref.groupby(pref_idx.hour)["Volume"].transform("mean") + 1)

    # On the overlap, legacy values should differ — proving leakage.
    overlap = legacy_full.index.intersection(legacy_pref.index)
    diff = (legacy_full.loc[overlap] - legacy_pref.loc[overlap]).abs()
    assert diff.max() > 1e-6, (
        "If this assertion fails, the legacy form somehow stopped leaking — "
        "either the test fixture changed, or pandas semantics changed. "
        "Investigate before assuming the new form is correct."
    )


def test_vol_ratio_hour_first_observation_is_neutral(synth_df, empty_market):
    """First-seen bar for each hour-of-day has no past samples to average; we
    fill the resulting NaN with 1.0 (neutral). Verify that.
    """
    full = build_features(synth_df, empty_market, empty_market, empty_market)
    # First bar of the dataset for each hour-of-day cannot have a meaningful
    # ratio. The implementation fills it with 1.0.
    first_per_hour = full.groupby(full.index.hour).head(1)
    assert (first_per_hour["vol_ratio_hour"] == 1.0).all(), \
        "First-seen bar per hour-of-day should be neutral (1.0)"
