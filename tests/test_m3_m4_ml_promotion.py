"""M-3 + M-4: ML promotion gate — AUC floor and prev.pkl rollback backup.

M-3: a degenerate model with AUC=0.0 must be rejected even when no incumbent exists.
M-4: when a model is promoted, the previous .pkl must be preserved as *_prev.pkl.
"""
from __future__ import annotations

import pickle
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from sklearn.dummy import DummyClassifier


def _make_val(n=100):
    rng = np.random.default_rng(0)
    X = pd.DataFrame({"f1": rng.random(n), "f2": rng.random(n)})
    y = pd.Series((rng.random(n) > 0.5).astype(int))
    return X, y


def _trained_dummy():
    X, y = _make_val()
    m = DummyClassifier(strategy="stratified", random_state=0)
    m.fit(X, y)
    return m


def _save_incumbent(path: Path, auc: float):
    with open(path, "wb") as f:
        pickle.dump({"model": _trained_dummy(), "features": ["f1", "f2"], "auc": auc}, f)


# ── M-3: AUC floor ────────────────────────────────────────────────────────────

class TestAUCFloor:
    """AUC=0.0 (degenerate model) must be rejected regardless of incumbent."""

    def test_daily_rejects_zero_auc_no_incumbent(self, tmp_path, monkeypatch):
        import models.ml_model as ml_model
        monkeypatch.setattr(ml_model, "MODEL_PATH", tmp_path / "model.pkl")
        X, y = _make_val()
        result = ml_model._save_if_better(_trained_dummy(), ["f1", "f2"], 0.0, X, y)
        assert result is False
        assert not (tmp_path / "model.pkl").exists()

    def test_daily_rejects_below_floor(self, tmp_path, monkeypatch):
        import models.ml_model as ml_model
        monkeypatch.setattr(ml_model, "MODEL_PATH", tmp_path / "model.pkl")
        X, y = _make_val()
        result = ml_model._save_if_better(_trained_dummy(), ["f1", "f2"], 0.54, X, y)
        assert result is False

    def test_daily_accepts_at_floor(self, tmp_path, monkeypatch):
        import models.ml_model as ml_model
        monkeypatch.setattr(ml_model, "MODEL_PATH", tmp_path / "model.pkl")
        X, y = _make_val()
        result = ml_model._save_if_better(_trained_dummy(), ["f1", "f2"], 0.55, X, y)
        assert result is True

    def test_intraday_rejects_zero_auc_no_incumbent(self, tmp_path, monkeypatch):
        import models.india_intraday_model as iim
        monkeypatch.setattr(iim, "MODEL_PATH", tmp_path / "model.pkl")
        X, y = _make_val()
        result = iim._save_if_better(_trained_dummy(), ["f1", "f2"], 0.0, X, y)
        assert result is False

    def test_intraday_accepts_at_floor(self, tmp_path, monkeypatch):
        import models.india_intraday_model as iim
        monkeypatch.setattr(iim, "MODEL_PATH", tmp_path / "model.pkl")
        X, y = _make_val()
        result = iim._save_if_better(_trained_dummy(), ["f1", "f2"], 0.55, X, y)
        assert result is True


# ── M-4: prev.pkl backup ──────────────────────────────────────────────────────

class TestPrevPklBackup:
    """When a model is promoted, the old .pkl must be renamed to *_prev.pkl."""

    def test_daily_creates_prev_pkl(self, tmp_path, monkeypatch):
        import models.ml_model as ml_model
        path = tmp_path / "model.pkl"
        monkeypatch.setattr(ml_model, "MODEL_PATH", path)
        _save_incumbent(path, auc=0.60)
        X, y = _make_val()
        ml_model._save_if_better(_trained_dummy(), ["f1", "f2"], 0.70, X, y)
        prev = path.with_suffix(".prev.pkl")
        assert prev.exists(), "_prev.pkl not created"
        with open(prev, "rb") as f:
            assert pickle.load(f)["auc"] == pytest.approx(0.60)

    def test_daily_no_prev_when_no_incumbent(self, tmp_path, monkeypatch):
        """First-ever promotion: no prev.pkl should be created (nothing to back up)."""
        import models.ml_model as ml_model
        path = tmp_path / "model.pkl"
        monkeypatch.setattr(ml_model, "MODEL_PATH", path)
        X, y = _make_val()
        ml_model._save_if_better(_trained_dummy(), ["f1", "f2"], 0.70, X, y)
        assert not path.with_suffix(".prev.pkl").exists()

    def test_intraday_creates_prev_pkl(self, tmp_path, monkeypatch):
        import models.india_intraday_model as iim
        path = tmp_path / "model.pkl"
        monkeypatch.setattr(iim, "MODEL_PATH", path)
        _save_incumbent(path, auc=0.60)
        X, y = _make_val()
        iim._save_if_better(_trained_dummy(), ["f1", "f2"], 0.70, X, y)
        prev = path.with_suffix(".prev.pkl")
        assert prev.exists()
        with open(prev, "rb") as f:
            assert pickle.load(f)["auc"] == pytest.approx(0.60)
