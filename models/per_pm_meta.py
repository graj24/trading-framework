"""
Per-PM stacked meta-learner (Stage 5).

Trains one GradientBoostingClassifier per PM on closed trades from
`paper_trades.db`. The model's inputs are the signal scores that were
present at trade entry (stored in the `signals_at_entry` JSON column);
its target is whether the trade was profitable.

This replaces the hard-coded `0.4 * ml + 0.3 * tech + …` weighting in
`_rule_based_decision` with a per-PM learned combination that adapts to
each PM's mandate, watchlist, and strategy.

Minimum trades for training: 30 closed trades per PM. Below this threshold,
`predict_proba` returns None and MasterAgent falls back to the existing
composite logic.

Usage:
    python models/per_pm_meta.py train [--pm PM_ID]
    python models/per_pm_meta.py predict --pm PM_ID --scores '{"ml_proba":0.6,...}'
"""
from __future__ import annotations

import json
import pickle
import sqlite3
import sys
import warnings
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

MODELS_DIR = Path("models") / "per_pm_meta"
DB_PATH    = Path("paper_trades.db")
MIN_TRADES = 30

# Input features drawn from the signals_at_entry JSON column.
# These are the calibrated upstream signal scores the meta-learner combines.
SIGNAL_FEATURES = [
    "ml_proba",
    "intraday_ml_proba",
    "technical_score",        # 0-10 legacy, or learned_tech_proba * 10
    "learned_tech_proba",     # Stage 4 optional, NaN if absent
    "sentiment",
    "pattern_ev",
    "win_rate",
    "regime_bull_proba",      # P(trending_bull) from Stage 3b, NaN if absent
    "regime_bear_proba",      # P(trending_bear)
]


def _load_trades(pm_id: str) -> pd.DataFrame:
    """Load closed trades with non-null signals_at_entry for a given PM."""
    if not DB_PATH.exists():
        return pd.DataFrame()
    try:
        conn = sqlite3.connect(DB_PATH)
        df = pd.read_sql_query(
            """
            SELECT outcome, pnl_pct, signals_at_entry
            FROM trades
            WHERE pm_id = ?
              AND outcome != 'open'
              AND signals_at_entry IS NOT NULL
              AND signals_at_entry != ''
            """,
            conn, params=(pm_id,)
        )
        conn.close()
        return df
    except Exception:
        return pd.DataFrame()


def _extract_features(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series]:
    """Parse the JSON signals column and build the feature matrix."""
    rows = []
    for _, row in df.iterrows():
        try:
            sig = json.loads(row["signals_at_entry"]) if isinstance(row["signals_at_entry"], str) else row["signals_at_entry"]
        except Exception:
            sig = {}
        feat = {
            "ml_proba":           sig.get("ml_proba"),
            "intraday_ml_proba":  sig.get("intraday_ml_proba"),
            "technical_score":    sig.get("technical_score"),
            "learned_tech_proba": sig.get("learned_tech_proba"),
            "sentiment":          sig.get("sentiment"),
            "pattern_ev":         sig.get("pattern_ev"),
            "win_rate":           sig.get("win_rate"),
        }
        # Expand regime_proba dict if present.
        rp = sig.get("regime_proba") or {}
        feat["regime_bull_proba"] = rp.get("trending_bull")
        feat["regime_bear_proba"] = rp.get("trending_bear")
        rows.append(feat)

    X = pd.DataFrame(rows, columns=SIGNAL_FEATURES).astype(float)
    y = (df["pnl_pct"] > 0).astype(int).reset_index(drop=True)
    return X, y


# ── Train ─────────────────────────────────────────────────────────────────────

def train(pm_ids: Optional[list[str]] = None) -> None:
    """Train one model per PM. Skips PMs below MIN_TRADES threshold."""
    from sklearn.calibration import CalibratedClassifierCV
    from sklearn.ensemble import GradientBoostingClassifier
    from sklearn.model_selection import TimeSeriesSplit

    # Discover PM IDs from DB if none specified.
    if pm_ids is None and DB_PATH.exists():
        try:
            conn = sqlite3.connect(DB_PATH)
            rows = conn.execute(
                "SELECT DISTINCT pm_id FROM trades WHERE outcome != 'open'"
            ).fetchall()
            conn.close()
            pm_ids = [r[0] for r in rows if r[0]]
        except Exception:
            pm_ids = []

    if not pm_ids:
        print("No PM IDs found in paper_trades.db. Nothing to train.")
        return

    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    for pm_id in pm_ids:
        df = _load_trades(str(pm_id))
        if len(df) < MIN_TRADES:
            print(f"PM{pm_id}: {len(df)} trades < {MIN_TRADES} minimum — skipping")
            continue

        X, y = _extract_features(df)
        X = X.fillna(0.0)  # impute missing signals with 0 (neutral/absent)
        base = GradientBoostingClassifier(n_estimators=100, max_depth=3,
                                          learning_rate=0.1, random_state=42)
        n_cal = max(10, len(X) // 4)
        fit_X, cal_X = X.iloc[:-n_cal], X.iloc[-n_cal:]
        fit_y, cal_y = y.iloc[:-n_cal], y.iloc[-n_cal:]
        if len(fit_X) < 10 or len(cal_X) < 5:
            print(f"PM{pm_id}: not enough rows for calibration split — skipping")
            continue
        base.fit(fit_X, fit_y)
        model = CalibratedClassifierCV(estimator=base, method="isotonic", cv="prefit")
        model.fit(cal_X, cal_y)

        path = MODELS_DIR / f"pm{pm_id}.pkl"
        with open(path, "wb") as f:
            pickle.dump({
                "model": model, "features": SIGNAL_FEATURES,
                "n_trades": len(X), "win_rate": float(y.mean()),
                "pm_id": str(pm_id),
            }, f)
        print(f"PM{pm_id}: saved to {path}")


# ── Predict ───────────────────────────────────────────────────────────────────

def predict_proba(pm_id: str, signals: dict) -> Optional[float]:
    """Return P(trade profitable) for the given PM and signal scores.

    Returns None if:
      - no model exists for this PM (below MIN_TRADES or not yet trained)
      - all input features are NaN (no signal data available)
    """
    path = MODELS_DIR / f"pm{pm_id}.pkl"
    if not path.exists():
        return None
    with open(path, "rb") as f:
        saved = pickle.load(f)
    model    = saved["model"]
    features = saved["features"]

    row: dict[str, Optional[float]] = {}
    rp = signals.get("regime_proba") or {}
    for feat in features:
        if feat == "regime_bull_proba":
            row[feat] = rp.get("trending_bull")
        elif feat == "regime_bear_proba":
            row[feat] = rp.get("trending_bear")
        else:
            row[feat] = signals.get(feat)

    X = pd.DataFrame([row], columns=features).astype(float)
    if X.isna().all(axis=None):
        return None
    X = X.fillna(0.0)
    return float(model.predict_proba(X)[0][1])


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("cmd", choices=["train", "predict"])
    parser.add_argument("--pm", type=str, default=None)
    parser.add_argument("--scores", type=str, default="{}")
    args = parser.parse_args()

    if args.cmd == "train":
        pms = [args.pm] if args.pm else None
        train(pm_ids=pms)
    else:
        if not args.pm:
            print("--pm required for predict"); sys.exit(1)
        scores = json.loads(args.scores)
        p = predict_proba(args.pm, scores)
        print(f"PM{args.pm}: meta_proba = {p:.4f}" if p is not None else
              f"PM{args.pm}: no model (needs {MIN_TRADES}+ trades)")
