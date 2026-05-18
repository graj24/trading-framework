"""
Stage 1C: walk-forward P&L promotion gate tests.

The gate must reject a model whose AUC clears the floor but whose simulated
net P&L (forward returns minus round-trip costs) is negative — the classic
"good ranking, bad timing/cost interaction" failure mode.
"""
from __future__ import annotations

import numpy as np
import pytest

from core.promotion_gate import WalkForwardPnL, walk_forward_pnl
from core.costs import ROUND_TRIP_COST_FRAC


def test_walk_forward_pnl_basic_arithmetic():
    """Sanity: 3 trades with +2% / -1% / +0.5% gross, cost 0.26% each."""
    proba = np.array([0.7, 0.7, 0.7, 0.4, 0.7])     # 4 fire, 1 doesn't
    fwd   = np.array([2.0, -1.0, 0.5, 99.0, 0.1])   # last is 0.1% gross

    wf = walk_forward_pnl(proba, fwd, threshold=0.55,
                          cost_per_round_trip_pct=0.26)

    assert wf.n_trades == 4
    # Gross sum (firing rows): 2 - 1 + 0.5 + 0.1 = 1.6
    # Cost: 4 trades * 0.26 = 1.04
    # Net: 1.6 - 1.04 = 0.56
    assert abs(wf.net_pnl_pct - 0.56) < 1e-9
    # Mean: 0.56 / 4 = 0.14
    assert abs(wf.mean_pnl_pct - 0.14) < 1e-9
    # Win rate: trades that were positive after cost
    # Net per trade: [2-0.26, -1-0.26, 0.5-0.26, 0.1-0.26] = [1.74, -1.26, 0.24, -0.16]
    # 2 positive of 4 → 50%
    assert abs(wf.win_rate_pct - 50.0) < 1e-9


def test_walk_forward_no_trades_returns_zeros():
    proba = np.array([0.1, 0.2, 0.3])
    fwd   = np.array([1.0, 1.0, 1.0])
    wf = walk_forward_pnl(proba, fwd, threshold=0.55)
    assert wf.n_trades == 0
    assert wf.net_pnl_pct == 0.0
    assert wf.mean_pnl_pct == 0.0


def test_walk_forward_default_cost_uses_core_costs():
    """When `cost_per_round_trip_pct` is None, falls back to core.costs."""
    proba = np.array([1.0])
    fwd   = np.array([1.0])
    wf = walk_forward_pnl(proba, fwd, threshold=0.5,
                          cost_per_round_trip_pct=None)
    expected_cost_pct = ROUND_TRIP_COST_FRAC * 100   # 0.26
    assert abs(wf.cost_per_trade_pct - expected_cost_pct) < 1e-9
    assert abs(wf.net_pnl_pct - (1.0 - expected_cost_pct)) < 1e-9


def test_walk_forward_last_n_slicing():
    proba = np.array([0.7] * 20)
    fwd   = np.array([1.0] * 10 + [-1.0] * 10)   # first 10 win, last 10 lose
    wf_all = walk_forward_pnl(proba, fwd, threshold=0.5,
                              cost_per_round_trip_pct=0.0)
    wf_tail = walk_forward_pnl(proba, fwd, threshold=0.5,
                               cost_per_round_trip_pct=0.0, last_n=10)
    # All 20: gross 0; tail 10: gross -10 (only losing rows considered).
    assert abs(wf_all.net_pnl_pct - 0.0) < 1e-9
    assert abs(wf_tail.net_pnl_pct - (-10.0)) < 1e-9


def test_passes_requires_min_trades_and_positive_pnl():
    # 3 trades, positive P&L → still rejected because below min_trades=5
    wf = WalkForwardPnL(n_eval=10, n_trades=3,
                        net_pnl_pct=2.0, mean_pnl_pct=0.67,
                        win_rate_pct=66.7, cost_per_trade_pct=0.26)
    ok, reason = wf.passes(min_net_pnl_pct=0.0, min_trades=5)
    assert not ok
    assert "trades" in reason

    # 6 trades, positive P&L → passes
    wf2 = WalkForwardPnL(n_eval=20, n_trades=6,
                         net_pnl_pct=1.0, mean_pnl_pct=0.17,
                         win_rate_pct=50.0, cost_per_trade_pct=0.26)
    ok2, reason2 = wf2.passes(min_net_pnl_pct=0.0, min_trades=5)
    assert ok2
    assert reason2 == "OK"

    # 6 trades, negative P&L → rejected
    wf3 = WalkForwardPnL(n_eval=20, n_trades=6,
                         net_pnl_pct=-0.5, mean_pnl_pct=-0.08,
                         win_rate_pct=33.3, cost_per_trade_pct=0.26)
    ok3, reason3 = wf3.passes(min_net_pnl_pct=0.0, min_trades=5)
    assert not ok3
    assert "P&L" in reason3


def test_save_if_better_rejects_negative_pnl_at_high_auc(tmp_path, monkeypatch):
    """End-to-end: a model with AUC=0.65 (clearly above floor) but negative
    walk-forward P&L should be rejected by _save_if_better."""
    import importlib
    import models.ml_model as mlm
    importlib.reload(mlm)
    monkeypatch.setattr(mlm, "MODEL_PATH", tmp_path / "model.pkl")

    # Real (tiny) sklearn model — pickleable.
    from sklearn.dummy import DummyClassifier
    import pandas as pd
    rng = np.random.default_rng(0)
    X_dummy = pd.DataFrame({"f1": rng.normal(0, 1, 50), "f2": rng.normal(0, 1, 50)})
    y_dummy = pd.Series(rng.integers(0, 2, 50))
    stub = DummyClassifier(strategy="prior").fit(X_dummy, y_dummy)

    # Walk-forward result with negative P&L despite plenty of trades.
    bad_wf = WalkForwardPnL(n_eval=100, n_trades=30,
                            net_pnl_pct=-2.5, mean_pnl_pct=-0.083,
                            win_rate_pct=35.0, cost_per_trade_pct=0.26)

    promoted = mlm._save_if_better(
        stub, ["f1", "f2"], 0.65, X_dummy.iloc[:20], y_dummy.iloc[:20],
        brier_uncal=0.20, brier_cal=0.18,
        walk_forward=bad_wf,
    )
    assert promoted is False, "Model with negative walk-forward P&L should be rejected"
    assert not (tmp_path / "model.pkl").exists(), \
        "Pickle should not be written when promotion is rejected"


def test_save_if_better_promotes_positive_pnl_at_high_auc(tmp_path, monkeypatch):
    """Mirror of the previous test: positive walk-forward P&L → promotion."""
    import importlib
    import models.ml_model as mlm
    importlib.reload(mlm)
    monkeypatch.setattr(mlm, "MODEL_PATH", tmp_path / "model.pkl")

    from sklearn.dummy import DummyClassifier
    import pandas as pd
    rng = np.random.default_rng(1)
    X_dummy = pd.DataFrame({"f1": rng.normal(0, 1, 50)})
    y_dummy = pd.Series(rng.integers(0, 2, 50))
    stub = DummyClassifier(strategy="prior").fit(X_dummy, y_dummy)

    good_wf = WalkForwardPnL(n_eval=100, n_trades=20,
                             net_pnl_pct=4.5, mean_pnl_pct=0.225,
                             win_rate_pct=60.0, cost_per_trade_pct=0.26)

    promoted = mlm._save_if_better(
        stub, ["f1"], 0.65, X_dummy.iloc[:20], y_dummy.iloc[:20],
        brier_uncal=0.20, brier_cal=0.18,
        walk_forward=good_wf,
    )
    assert promoted is True
    assert (tmp_path / "model.pkl").exists()

    import pickle
    payload = pickle.loads((tmp_path / "model.pkl").read_bytes())
    assert "walk_forward" in payload
    assert payload["walk_forward"]["net_pnl_pct"] == 4.5
    assert payload["walk_forward"]["n_trades"] == 20
