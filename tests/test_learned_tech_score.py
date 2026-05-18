"""
Stage 4 tests — learned_tech_score model.

Tests:
  * build_features returns expected columns and NaN-free for sufficient data
  * train() smoke-tests cleanly on synthetic data
  * predict_proba returns float in [0,1] when model present, None otherwise
  * calibrated output is in [0,1] (sanity check on the pipeline)
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest


@pytest.fixture(scope="module")
def synth_ohlcv() -> pd.DataFrame:
    rng = np.random.default_rng(0)
    n = 500
    rets = rng.normal(0, 0.012, n)
    close = pd.Series(100 * np.exp(np.cumsum(rets)),
                      index=pd.date_range("2022-01-01", periods=n, freq="B"))
    high = close * (1 + rng.uniform(0, 0.01, n))
    low  = close * (1 - rng.uniform(0, 0.01, n))
    vol  = pd.Series(rng.integers(100_000, 1_000_000, n).astype(float), index=close.index)
    return pd.DataFrame({"Open": close, "High": high, "Low": low,
                         "Close": close, "Volume": vol})


def test_build_features_columns(synth_ohlcv):
    from models.learned_tech_score import build_features
    feat = build_features(synth_ohlcv)
    expected = {"ema20_ratio", "ema50_ratio", "rsi_14", "macd_hist",
                "atr_pct", "bb_pos", "vol_ratio", "adx_14"}
    assert expected.issubset(set(feat.columns))


def test_build_features_no_nans_in_tail(synth_ohlcv):
    from models.learned_tech_score import build_features
    feat = build_features(synth_ohlcv)
    # Last 200 bars (past warmup) should have no NaN.
    assert feat.tail(200).isna().sum().sum() == 0


def test_train_runs_and_saves_pickle(tmp_path, monkeypatch, synth_ohlcv):
    from models import learned_tech_score as lts
    monkeypatch.setattr(lts, "MODEL_PATH", tmp_path / "lts.pkl")
    # Patch stocks glob so it uses our synthetic fixture.
    monkeypatch.setattr(
        "pathlib.Path.glob",
        lambda self, pattern: [tmp_path / "SYNTH" / "price_history.parquet"]
        if "price_history" in pattern else [],
    )
    sym_dir = tmp_path / "SYNTH"
    sym_dir.mkdir()
    synth_ohlcv.to_parquet(sym_dir / "price_history.parquet")

    lts.train()
    assert (tmp_path / "lts.pkl").exists()


def test_predict_proba_returns_none_when_no_model(tmp_path, monkeypatch, synth_ohlcv):
    from models import learned_tech_score as lts
    monkeypatch.setattr(lts, "MODEL_PATH", tmp_path / "nonexistent.pkl")
    assert lts.predict_proba(synth_ohlcv) is None


def test_predict_proba_in_unit_interval(tmp_path, monkeypatch, synth_ohlcv):
    from models import learned_tech_score as lts
    monkeypatch.setattr(lts, "MODEL_PATH", tmp_path / "lts.pkl")
    # Train synthetic model and then predict.
    lts._train_on_synthetic.__module__  # confirm it exists
    # Directly call synthetic training.
    orig_path = lts.MODEL_PATH
    lts.MODEL_PATH = tmp_path / "lts.pkl"
    lts._train_on_synthetic()
    p = lts.predict_proba(synth_ohlcv)
    lts.MODEL_PATH = orig_path
    assert p is not None
    assert 0.0 <= p <= 1.0


def test_calibrated_output_differs_across_bars(tmp_path, monkeypatch, synth_ohlcv):
    """The model should not output a constant — probabilities must vary
    with the indicator state across different market conditions."""
    from models import learned_tech_score as lts
    lts.MODEL_PATH = tmp_path / "lts_vary.pkl"
    lts._train_on_synthetic()

    # Predict on different 300-bar windows — probabilities must not all be identical.
    probas = set()
    n = len(synth_ohlcv)
    for start in range(0, min(n - 210, 300), 50):
        sub = synth_ohlcv.iloc[start: start + 210]
        p = lts.predict_proba(sub)
        if p is not None:
            probas.add(round(p, 3))
    lts.MODEL_PATH = Path("models") / "learned_tech_score.pkl"

    assert len(probas) > 1, "Calibrated tech score must vary across different market windows"
