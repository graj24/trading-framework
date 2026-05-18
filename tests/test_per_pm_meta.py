"""
Stage 5 tests — per-PM stacked meta-learner.

Tests:
  * _extract_features parses JSON signals into the expected column set
  * predict_proba returns None when no model file exists (graceful fallback)
  * train() on synthetic DB produces a loadable model when trades >= MIN_TRADES
  * train() skips a PM with fewer than MIN_TRADES trades
  * predict_proba returns float in [0,1] after training
  * predict_proba returns None when all input signals are NaN
"""
from __future__ import annotations

import json
import pickle
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from models.per_pm_meta import MIN_TRADES, SIGNAL_FEATURES


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_trades_db(tmp_path: Path, pm_id: str, n_trades: int,
                     win_rate: float = 0.55) -> Path:
    """Create a paper_trades.db with n_trades rows for the given PM."""
    rng = np.random.default_rng(abs(hash(pm_id)) % (2**31))
    db = tmp_path / "paper_trades.db"
    conn = sqlite3.connect(db)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pm_id TEXT,
            symbol TEXT,
            outcome TEXT,
            pnl_pct REAL,
            signals_at_entry TEXT,
            entry_date TEXT
        )
    """)
    for i in range(n_trades):
        pnl = rng.uniform(0.5, 3.0) if rng.random() < win_rate else rng.uniform(-2.0, -0.1)
        sigs = {
            "ml_proba":          round(rng.uniform(0.4, 0.8), 3),
            "intraday_ml_proba": round(rng.uniform(0.4, 0.8), 3),
            "technical_score":   round(rng.uniform(4, 9), 1),
            "sentiment":         round(rng.uniform(-0.5, 0.8), 3),
            "pattern_ev":        round(rng.uniform(-1, 3), 2),
            "win_rate":          round(rng.uniform(45, 70), 1),
            "regime_proba": {
                "trending_bull": round(rng.uniform(0.3, 0.7), 3),
                "trending_bear": round(rng.uniform(0.1, 0.3), 3),
            },
        }
        conn.execute(
            "INSERT INTO trades (pm_id, symbol, outcome, pnl_pct, signals_at_entry, entry_date) VALUES (?,?,?,?,?,?)",
            (pm_id, "RELIANCE", "closed", round(pnl, 3), json.dumps(sigs), "2025-01-01"),
        )
    conn.commit(); conn.close()
    return db


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_extract_features_returns_all_columns():
    from models.per_pm_meta import _extract_features
    df = pd.DataFrame([{
        "outcome": "closed",
        "pnl_pct": 1.5,
        "signals_at_entry": json.dumps({
            "ml_proba": 0.65, "intraday_ml_proba": 0.58,
            "technical_score": 7, "sentiment": 0.3,
            "pattern_ev": 1.2, "win_rate": 58,
            "regime_proba": {"trending_bull": 0.6, "trending_bear": 0.1},
        })
    }])
    X, y = _extract_features(df)
    assert set(SIGNAL_FEATURES) == set(X.columns)
    assert len(X) == 1
    assert int(y.iloc[0]) == 1   # pnl > 0 → win


def test_predict_proba_returns_none_when_no_model(tmp_path, monkeypatch):
    from models import per_pm_meta as ppm
    monkeypatch.setattr(ppm, "MODELS_DIR", tmp_path / "no_models")
    assert ppm.predict_proba("1", {"ml_proba": 0.7}) is None


def test_train_creates_model_for_pm_with_enough_trades(tmp_path, monkeypatch):
    from models import per_pm_meta as ppm
    db = _make_trades_db(tmp_path, "1", n_trades=MIN_TRADES + 10)
    monkeypatch.setattr(ppm, "DB_PATH",    db)
    monkeypatch.setattr(ppm, "MODELS_DIR", tmp_path / "models")
    ppm.train(pm_ids=["1"])
    assert (tmp_path / "models" / "pm1.pkl").exists()


def test_train_skips_pm_below_min_trades(tmp_path, monkeypatch):
    from models import per_pm_meta as ppm
    db = _make_trades_db(tmp_path, "2", n_trades=MIN_TRADES - 1)
    monkeypatch.setattr(ppm, "DB_PATH",    db)
    monkeypatch.setattr(ppm, "MODELS_DIR", tmp_path / "models")
    ppm.train(pm_ids=["2"])
    assert not (tmp_path / "models" / "pm2.pkl").exists()


def test_predict_proba_in_unit_interval(tmp_path, monkeypatch):
    from models import per_pm_meta as ppm
    db = _make_trades_db(tmp_path, "3", n_trades=MIN_TRADES + 20)
    monkeypatch.setattr(ppm, "DB_PATH",    db)
    monkeypatch.setattr(ppm, "MODELS_DIR", tmp_path / "models")
    ppm.train(pm_ids=["3"])
    p = ppm.predict_proba("3", {
        "ml_proba": 0.72, "intraday_ml_proba": 0.61,
        "technical_score": 7, "sentiment": 0.4,
        "pattern_ev": 1.5, "win_rate": 60,
        "regime_proba": {"trending_bull": 0.65, "trending_bear": 0.1},
    })
    assert p is not None
    assert 0.0 <= p <= 1.0


def test_predict_proba_returns_none_for_all_nan_signals(tmp_path, monkeypatch):
    from models import per_pm_meta as ppm
    db = _make_trades_db(tmp_path, "4", n_trades=MIN_TRADES + 5)
    monkeypatch.setattr(ppm, "DB_PATH",    db)
    monkeypatch.setattr(ppm, "MODELS_DIR", tmp_path / "models")
    ppm.train(pm_ids=["4"])
    p = ppm.predict_proba("4", {})   # all signals absent
    # All NaN signals → model returns None (no signal to act on)
    assert p is None
