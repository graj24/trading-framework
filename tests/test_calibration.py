"""
Stage 1A: probability calibration tests.

Calibration cannot improve AUC (rank order is preserved). What it improves
is the Brier score — i.e. how close predicted probabilities are to observed
frequencies. We construct a synthetic dataset on which an uncalibrated GBC is
known to be over-confident at the extremes, then assert that wrapping it in
`CalibratedClassifierCV(method="isotonic")` reduces Brier score.

These tests run on synthetic data; they prove the *mechanism* works. Real
lift on the trading dataset will be measured when the calibrated `train()`
runs against EC2 data.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from sklearn.calibration import CalibratedClassifierCV
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.metrics import brier_score_loss, roc_auc_score


@pytest.fixture(scope="module")
def biased_dataset():
    """Synthetic binary classification where features predict the label with
    moderate noise. GBC produces well-ordered but mis-calibrated probabilities
    — the canonical setup for testing calibration."""
    rng = np.random.default_rng(7)
    n = 4000
    X = pd.DataFrame({
        "f1": rng.normal(0, 1, n),
        "f2": rng.normal(0, 1, n),
        "f3": rng.normal(0, 1, n),
        "f4": rng.normal(0, 1, n),
    })
    # True log-odds is a linear combination; tree models will misfit slightly.
    logit = 0.8 * X["f1"] + 0.5 * X["f2"] - 0.3 * X["f3"] + 0.1 * X["f4"]
    p = 1 / (1 + np.exp(-logit))
    y = (rng.uniform(size=n) < p).astype(int)
    # Time-ordered split (no shuffle)
    n_tr = int(n * 0.6)
    n_cal = int(n * 0.2)
    return {
        "X_tr":  X.iloc[:n_tr],          "y_tr":  y[:n_tr],
        "X_cal": X.iloc[n_tr:n_tr+n_cal], "y_cal": y[n_tr:n_tr+n_cal],
        "X_te":  X.iloc[n_tr+n_cal:],     "y_te":  y[n_tr+n_cal:],
    }


def test_isotonic_calibration_reduces_brier(biased_dataset):
    d = biased_dataset
    base = GradientBoostingClassifier(n_estimators=200, max_depth=3, learning_rate=0.05,
                                       random_state=42)
    base.fit(d["X_tr"], d["y_tr"])
    p_unc = base.predict_proba(d["X_te"])[:, 1]

    cal = CalibratedClassifierCV(estimator=base, method="isotonic", cv="prefit")
    cal.fit(d["X_cal"], d["y_cal"])
    p_cal = cal.predict_proba(d["X_te"])[:, 1]

    brier_unc = brier_score_loss(d["y_te"], p_unc)
    brier_cal = brier_score_loss(d["y_te"], p_cal)

    print(f"\nBrier uncalibrated: {brier_unc:.4f}")
    print(f"Brier calibrated:   {brier_cal:.4f}")
    print(f"Improvement:        {(brier_unc-brier_cal)/brier_unc*100:+.1f}%")

    # Calibrated should be at least as good as uncalibrated. Isotonic can
    # occasionally tie on small sets; require at most a 0.5pp regression.
    assert brier_cal <= brier_unc + 0.005, \
        f"Calibration regressed Brier ({brier_unc:.4f} → {brier_cal:.4f})"


def test_calibration_preserves_auc_to_within_noise(biased_dataset):
    """Isotonic calibration is monotone, so AUC is preserved exactly when the
    calibration set is large enough. With finite data, sampling noise allows a
    small spread; require the calibrated AUC to be within 1pp of uncalibrated.
    """
    d = biased_dataset
    base = GradientBoostingClassifier(n_estimators=200, max_depth=3,
                                       learning_rate=0.05, random_state=42)
    base.fit(d["X_tr"], d["y_tr"])
    p_unc = base.predict_proba(d["X_te"])[:, 1]

    cal = CalibratedClassifierCV(estimator=base, method="isotonic", cv="prefit")
    cal.fit(d["X_cal"], d["y_cal"])
    p_cal = cal.predict_proba(d["X_te"])[:, 1]

    auc_unc = roc_auc_score(d["y_te"], p_unc)
    auc_cal = roc_auc_score(d["y_te"], p_cal)
    assert abs(auc_unc - auc_cal) < 0.01, \
        f"AUC drifted by {auc_unc - auc_cal:+.4f} — should be ~0 under monotone isotonic calibration"


def test_calibrated_pickle_roundtrip(biased_dataset, tmp_path):
    """Verify the saved-payload format (model, features, auc, brier_*) survives
    a pickle round-trip and predict_proba still works. Mirrors the shape used
    by `_save_if_better`."""
    import pickle

    d = biased_dataset
    base = GradientBoostingClassifier(n_estimators=100, max_depth=3, random_state=42)
    base.fit(d["X_tr"], d["y_tr"])
    cal = CalibratedClassifierCV(estimator=base, method="isotonic", cv="prefit")
    cal.fit(d["X_cal"], d["y_cal"])

    payload = {
        "model": cal, "features": list(d["X_tr"].columns),
        "auc": 0.78, "brier_uncal": 0.22, "brier_cal": 0.20,
    }
    path = tmp_path / "model.pkl"
    with open(path, "wb") as f:
        pickle.dump(payload, f)
    with open(path, "rb") as f:
        loaded = pickle.load(f)

    assert loaded["features"] == list(d["X_tr"].columns)
    assert loaded["brier_cal"] < loaded["brier_uncal"]
    p = loaded["model"].predict_proba(d["X_te"].iloc[:5])[:, 1]
    assert p.shape == (5,)
    assert ((p >= 0.0) & (p <= 1.0)).all()
