"""Tests for ML promotion gate (AUC-delta check before overwriting model.pkl)."""
from __future__ import annotations

import pickle
import numpy as np
import pandas as pd
import pytest
from pathlib import Path
from sklearn.dummy import DummyClassifier


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_val(n=100):
    rng = np.random.default_rng(0)
    X = pd.DataFrame({"f1": rng.random(n), "f2": rng.random(n)})
    y = pd.Series((rng.random(n) > 0.5).astype(int))
    return X, y


def _trained_dummy(strategy="stratified", seed=0):
    """Return a fitted, picklable DummyClassifier."""
    X, y = _make_val()
    m = DummyClassifier(strategy=strategy, random_state=seed)
    m.fit(X, y)
    return m


def _save_incumbent(path: Path, auc: float):
    m = _trained_dummy()
    with open(path, "wb") as f:
        pickle.dump({"model": m, "features": ["f1", "f2"], "auc": auc}, f)


# ── daily model ───────────────────────────────────────────────────────────────

class TestDailyPromotionGate:
    def test_no_incumbent_always_saves(self, tmp_path, monkeypatch):
        import ml_model
        monkeypatch.setattr(ml_model, "MODEL_PATH", tmp_path / "model.pkl")
        X, y = _make_val()
        result = ml_model._save_if_better(_trained_dummy(), ["f1", "f2"], 0.65, X, y)
        assert result is True
        assert (tmp_path / "model.pkl").exists()

    def test_better_model_saves(self, tmp_path, monkeypatch):
        import ml_model
        path = tmp_path / "model.pkl"
        monkeypatch.setattr(ml_model, "MODEL_PATH", path)
        # Incumbent with AUC stored as 0.50; new model claims 0.65
        _save_incumbent(path, auc=0.50)
        X, y = _make_val()
        result = ml_model._save_if_better(_trained_dummy(), ["f1", "f2"], 0.65, X, y)
        assert result is True
        with open(path, "rb") as f:
            assert pickle.load(f)["auc"] == pytest.approx(0.65)

    def test_worse_model_rejected(self, tmp_path, monkeypatch):
        import ml_model
        path = tmp_path / "model.pkl"
        monkeypatch.setattr(ml_model, "MODEL_PATH", path)
        _save_incumbent(path, auc=0.80)
        X, y = _make_val()
        result = ml_model._save_if_better(_trained_dummy(), ["f1", "f2"], 0.50, X, y)
        assert result is False
        # File must still hold the old model
        with open(path, "rb") as f:
            assert pickle.load(f)["auc"] == pytest.approx(0.80)

    def test_within_delta_saves(self, tmp_path, monkeypatch):
        """new_auc == incumbent_auc + MIN_AUC_DELTA should be accepted."""
        import ml_model
        path = tmp_path / "model.pkl"
        monkeypatch.setattr(ml_model, "MODEL_PATH", path)
        _save_incumbent(path, auc=0.60)
        X, y = _make_val()
        new_auc = 0.60 + ml_model.MIN_AUC_DELTA  # exactly at boundary
        result = ml_model._save_if_better(_trained_dummy(), ["f1", "f2"], new_auc, X, y)
        assert result is True

    def test_saved_payload_includes_auc(self, tmp_path, monkeypatch):
        import ml_model
        path = tmp_path / "model.pkl"
        monkeypatch.setattr(ml_model, "MODEL_PATH", path)
        X, y = _make_val()
        ml_model._save_if_better(_trained_dummy(), ["f1", "f2"], 0.72, X, y)
        with open(path, "rb") as f:
            saved = pickle.load(f)
        assert saved["auc"] == pytest.approx(0.72)
        assert saved["features"] == ["f1", "f2"]


# ── intraday model ────────────────────────────────────────────────────────────

class TestIntradayPromotionGate:
    def test_no_incumbent_always_saves(self, tmp_path, monkeypatch):
        import india_intraday_model as iim
        monkeypatch.setattr(iim, "MODEL_PATH", tmp_path / "model.pkl")
        X, y = _make_val()
        result = iim._save_if_better(_trained_dummy(), ["f1", "f2"], 0.65, X, y)
        assert result is True
        assert (tmp_path / "model.pkl").exists()

    def test_worse_model_rejected(self, tmp_path, monkeypatch):
        import india_intraday_model as iim
        path = tmp_path / "model.pkl"
        monkeypatch.setattr(iim, "MODEL_PATH", path)
        _save_incumbent(path, auc=0.80)
        X, y = _make_val()
        result = iim._save_if_better(_trained_dummy(), ["f1", "f2"], 0.50, X, y)
        assert result is False

    def test_saved_payload_includes_auc(self, tmp_path, monkeypatch):
        import india_intraday_model as iim
        path = tmp_path / "model.pkl"
        monkeypatch.setattr(iim, "MODEL_PATH", path)
        X, y = _make_val()
        iim._save_if_better(_trained_dummy(), ["f1", "f2"], 0.72, X, y)
        with open(path, "rb") as f:
            saved = pickle.load(f)
        assert saved["auc"] == pytest.approx(0.72)
        assert saved["features"] == ["f1", "f2"]
