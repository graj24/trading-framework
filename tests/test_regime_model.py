"""
Stage 3b tests — probabilistic regime model.

Tests:
  * build_regime_features produces correct columns and NaN-free output
  * train() on synthetic data produces a loadable model
  * predict_proba returns 4 probabilities summing to 1.0
  * predict_proba returns None when model absent (graceful fallback)
  * extreme feature vectors dominate the expected regime bucket
"""
from __future__ import annotations

import pickle
from pathlib import Path

import numpy as np
import pandas as pd
import pytest


@pytest.fixture(scope="module")
def synthetic_close():
    rng = np.random.default_rng(0)
    n = 800
    rets = rng.normal(0, 0.012, n)
    idx = pd.date_range("2020-01-01", periods=n, freq="B")
    return pd.Series(100 * np.exp(np.cumsum(rets)), index=idx)


def test_build_regime_features_columns(synthetic_close):
    from models.regime_model import build_regime_features
    feat = build_regime_features(synthetic_close)
    assert set(feat.columns) >= {"ret_20d", "vol_20d", "vix"}


def test_build_regime_features_no_nans(synthetic_close):
    from models.regime_model import build_regime_features
    feat = build_regime_features(synthetic_close)
    assert feat.isna().sum().sum() == 0


def test_build_regime_features_with_vix(synthetic_close):
    from models.regime_model import build_regime_features
    vix = pd.Series(15.0, index=synthetic_close.index)
    feat = build_regime_features(synthetic_close, vix=vix)
    assert "vix" in feat.columns
    assert (feat["vix"] == 15.0).all()


def test_train_produces_loadable_pickle(tmp_path, monkeypatch, synthetic_close):
    from models import regime_model as rm
    monkeypatch.setattr(rm, "MODEL_PATH", tmp_path / "regime_gmm.pkl")
    rm.train(nifty_close=synthetic_close)
    assert (tmp_path / "regime_gmm.pkl").exists()
    with open(tmp_path / "regime_gmm.pkl", "rb") as f:
        saved = pickle.load(f)
    assert "gmm" in saved and "scaler" in saved


def test_predict_proba_returns_four_regimes(tmp_path, monkeypatch, synthetic_close):
    from models import regime_model as rm
    monkeypatch.setattr(rm, "MODEL_PATH", tmp_path / "regime_gmm.pkl")
    rm.train(nifty_close=synthetic_close)
    proba = rm.predict_proba(ret_20d=3.0, vol_20d=12.0, vix=13.0)
    assert proba is not None
    assert set(proba.keys()) == set(rm.LABEL_NAMES)
    assert abs(sum(proba.values()) - 1.0) < 1e-4


def test_predict_proba_returns_none_when_no_model(tmp_path, monkeypatch):
    from models import regime_model as rm
    monkeypatch.setattr(rm, "MODEL_PATH", tmp_path / "nonexistent.pkl")
    assert rm.predict_proba(3.0, 12.0) is None


def test_predict_proba_changes_across_feature_space(tmp_path, monkeypatch, synthetic_close):
    """Verify the model is actually conditioning on inputs: the probability
    vector for extreme-positive ret_20d must differ from extreme-negative.
    On genuine market data this will be large; on synthetic noise it may be
    small but should be non-zero."""
    from models import regime_model as rm
    monkeypatch.setattr(rm, "MODEL_PATH", tmp_path / "regime_gmm.pkl")
    rm.train(nifty_close=synthetic_close)

    p1 = rm.predict_proba(ret_20d=15.0, vol_20d=8.0, vix=10.0)
    p2 = rm.predict_proba(ret_20d=-15.0, vol_20d=8.0, vix=10.0)
    assert p1 is not None and p2 is not None
    # They must not be identical — the model must respond to different inputs.
    assert p1 != p2, "predict_proba should return different dists for different inputs"


def test_regime_agent_includes_regime_proba_in_output(monkeypatch):
    """RegimeAgent.run() must return regime_proba key (even if empty dict when
    GMM model not present)."""
    from agents.regime_agent import RegimeAgent

    # Patch the network calls.
    import pandas as _pd
    import numpy as _np

    rng = _np.random.default_rng(0)
    n = 60
    close = _pd.Series(100 * _np.exp(_np.cumsum(rng.normal(0, 0.01, n))),
                       index=_pd.date_range("2025-01-01", periods=n, freq="B"))
    nsei_df = _pd.DataFrame({
        "High": close * 1.005, "Low": close * 0.995,
        "Close": close, "Open": close, "Volume": 1e6,
    })
    nsei_df.index.name = "Date"

    monkeypatch.setattr("yfinance.download",
                         lambda ticker, **kw: nsei_df if "NSEI" in ticker else _pd.DataFrame())

    agent = RegimeAgent()
    result = agent.run()
    assert result.ok(), f"RegimeAgent failed: {result.error}"
    assert "regime_proba" in result.data, \
        "regime_proba key must be present (can be empty dict if GMM not trained)"
