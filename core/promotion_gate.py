"""
Walk-forward P&L promotion gate (Stage 1C).

Probability calibration (Stage 1A) makes proba thresholds *meaningful*; this
module makes promotion *gated on money*, not just AUC.

When `train()` finishes, we have a calibrated model and a validation set.
This helper simulates trading the candidate's signals over that validation
slice with realistic round-trip costs from `core.costs`. If net P&L is
negative — even at high AUC — promotion is rejected.

This catches the classic failure mode where a model has higher AUC than the
incumbent but worse timing/cost interaction (more frequent triggers at
marginal-edge points → costs eat the alpha).

Used by `models/ml_model.py` and `models/india_intraday_model.py`.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np

from core.costs import ROUND_TRIP_COST_FRAC


@dataclass
class WalkForwardPnL:
    """Result of a walk-forward P&L simulation on a held-out slice."""
    n_eval:        int    # rows considered
    n_trades:      int    # rows where proba >= threshold
    net_pnl_pct:   float  # sum of (fwd_return - cost) over triggered trades, in %
    mean_pnl_pct:  float  # mean net per trade
    win_rate_pct:  float  # % of triggered trades that closed positive
    cost_per_trade_pct: float

    def passes(self, min_net_pnl_pct: float = 0.0,
               min_trades: int = 5) -> tuple[bool, str]:
        """Decide whether the gate is satisfied.

        Defaults: require at least 5 trades AND non-negative net P&L. With
        fewer than 5 trades the result is too noisy to base promotion on.
        """
        if self.n_trades < min_trades:
            return False, f"only {self.n_trades} trades < min {min_trades}"
        if self.net_pnl_pct < min_net_pnl_pct:
            return False, f"net P&L {self.net_pnl_pct:+.2f}% < min {min_net_pnl_pct:+.2f}%"
        return True, "OK"


def walk_forward_pnl(
    proba: np.ndarray,
    fwd_returns_pct: np.ndarray,
    threshold: float = 0.55,
    cost_per_round_trip_pct: Optional[float] = None,
    last_n: Optional[int] = None,
) -> WalkForwardPnL:
    """Simulate trading the candidate's calibrated signal.

    Parameters
    ----------
    proba
        Calibrated `predict_proba(...)[:, 1]` outputs from the candidate model.
    fwd_returns_pct
        The realised forward return at the model's horizon, in PERCENT
        (i.e. +1.5 for a +1.5% return). Same length and ordering as `proba`.
    threshold
        Trade when `proba >= threshold`. Default matches the BUY cutoff
        used by the live decision pipeline (0.55).
    cost_per_round_trip_pct
        Round-trip cost in %. Defaults to `core.costs.ROUND_TRIP_COST_FRAC`
        (slippage 5bps × 2 sides + brokerage 3bps × 2 sides + STT 10bps on sell)
        which is currently 0.26% per round trip.
    last_n
        Only consider the last N rows (most recent slice). If None, evaluate
        the full input.

    Returns
    -------
    WalkForwardPnL with aggregate statistics.
    """
    proba = np.asarray(proba, dtype=float)
    fwd = np.asarray(fwd_returns_pct, dtype=float)
    if proba.shape != fwd.shape:
        raise ValueError(f"proba and fwd_returns_pct shape mismatch: "
                          f"{proba.shape} vs {fwd.shape}")
    if last_n is not None and last_n > 0 and last_n < len(proba):
        proba = proba[-last_n:]
        fwd = fwd[-last_n:]

    if cost_per_round_trip_pct is None:
        cost_per_round_trip_pct = ROUND_TRIP_COST_FRAC * 100   # frac → %

    fired = proba >= threshold
    valid = ~np.isnan(fwd)
    fired = fired & valid

    n_trades = int(fired.sum())
    if n_trades == 0:
        return WalkForwardPnL(
            n_eval=int(valid.sum()),
            n_trades=0, net_pnl_pct=0.0, mean_pnl_pct=0.0, win_rate_pct=0.0,
            cost_per_trade_pct=cost_per_round_trip_pct,
        )

    net_per_trade = fwd[fired] - cost_per_round_trip_pct
    return WalkForwardPnL(
        n_eval=int(valid.sum()),
        n_trades=n_trades,
        net_pnl_pct=float(net_per_trade.sum()),
        mean_pnl_pct=float(net_per_trade.mean()),
        win_rate_pct=float((net_per_trade > 0).mean() * 100),
        cost_per_trade_pct=cost_per_round_trip_pct,
    )
