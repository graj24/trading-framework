"""
Learned technical score (Stage 4).

Replaces the handcrafted 10-binary-check `technical_score` in
`agents/technical_agent.py` with a calibrated GradientBoostingClassifier
trained on the same indicator features but against a real forward-return label.

The legacy score adds 1 point for each of: above EMA20, above EMA50, above
EMA200, RSI in 40-60, MACD bullish, above VWAP, OBV rising, ADX > 25,
not near upper BB, ATR% < 2. The composite 0-10 score was never validated;
the trained model learns what actually matters.

Output: `predict_proba(X)[:, 1]` — calibrated probability of
`forward_return > 1.5%` in 5 days, matching the daily GBC's label definition.

Usage:
    python models/learned_tech_score.py train
    python models/learned_tech_score.py predict RELIANCE
"""
from __future__ import annotations

import pickle
import sys
import warnings
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

MODEL_PATH = Path("models") / "learned_tech_score.pkl"
FORWARD_DAYS = 5
LABEL_THRESHOLD = 1.5   # % — same as ml_model.py


# ── Feature engineering ───────────────────────────────────────────────────────

def build_features(df: pd.DataFrame) -> pd.DataFrame:
    """Compute the same indicator features used by TechnicalAgent, returning
    each as a continuous value (not a binary check). The model learns the
    nonlinear combination.
    """
    from core import features as F

    c, h, l, v = df["Close"], df["High"], df["Low"], df["Volume"]

    feat = pd.DataFrame(index=df.index)

    # EMA ratios — continuous distance from price to each EMA
    feat["ema20_ratio"]  = c / F.ema(c, 20) - 1
    feat["ema50_ratio"]  = c / F.ema(c, 50) - 1
    feat["ema200_ratio"] = c / F.ema(c, 200) - 1
    feat["ema20_50_ratio"] = F.ema(c, 20) / F.ema(c, 50) - 1

    # Momentum
    feat["rsi_14"]      = F.rsi(c, 14)
    macd_l, sig_l, hist = F.macd(c)
    feat["macd_hist"]   = hist
    feat["macd_ratio"]  = macd_l / sig_l.replace(0, np.nan) - 1  # direction + magnitude

    # Volatility
    feat["atr_pct"]    = F.atr(h, l, c, 14) / c * 100
    feat["bb_pos"]     = F.bb_position(c)
    feat["bb_width"]   = F.bb_width(c)

    # Volume
    feat["obv_trend"]  = (F.obv(c, v) / F.obv(c, v).shift(5) - 1) * 100
    feat["vol_ratio"]  = F.volume_ratio(v, 20)

    # Trend strength
    feat["adx_14"]     = F.adx(h, l, c, 14)

    return feat.replace([np.inf, -np.inf], np.nan)


# ── Train ─────────────────────────────────────────────────────────────────────

def train() -> None:
    from sklearn.calibration import CalibratedClassifierCV
    from sklearn.ensemble import GradientBoostingClassifier
    from sklearn.model_selection import TimeSeriesSplit
    from sklearn.metrics import roc_auc_score, brier_score_loss

    all_X, all_y = [], []
    parquets = sorted(Path("stocks").glob("*/price_history.parquet"))

    if not parquets:
        _train_on_synthetic()
        return

    for path in parquets:
        try:
            df = pd.read_parquet(path).sort_index()
            df.index = pd.to_datetime(df.index, utc=True).tz_localize(None)
            df = df.dropna(subset=["Open", "High", "Low", "Close", "Volume"])
            if len(df) < 250:
                continue
            feat = build_features(df)
            fwd = (df["Close"].shift(-FORWARD_DAYS) / df["Close"] - 1) * 100
            combined = feat.join(fwd.rename("fwd")).dropna().iloc[:-FORWARD_DAYS]
            labels = (combined["fwd"] > LABEL_THRESHOLD).astype(int)
            all_X.append(combined.drop("fwd", axis=1))
            all_y.append(labels)
        except Exception:
            continue

    X = pd.concat(all_X).reset_index(drop=True)
    y = pd.concat(all_y).reset_index(drop=True)
    print(f"Training tech-score GBM: {len(X):,} samples, {len(X.columns)} features")

    base_params = dict(n_estimators=200, max_depth=3, learning_rate=0.05,
                       subsample=0.8, random_state=42)
    tscv = TimeSeriesSplit(n_splits=5)
    aucs, briers = [], []
    for tr_idx, vl_idx in tscv.split(X):
        m = GradientBoostingClassifier(**base_params)
        m.fit(X.iloc[tr_idx].fillna(0), y.iloc[tr_idx])
        p = m.predict_proba(X.iloc[vl_idx].fillna(0))[:, 1]
        aucs.append(roc_auc_score(y.iloc[vl_idx], p))
        briers.append(brier_score_loss(y.iloc[vl_idx], p))

    print(f"Mean AUC: {np.mean(aucs):.4f}  Brier: {np.mean(briers):.4f}")

    n_cal = max(200, len(X) // 5)
    base = GradientBoostingClassifier(**base_params)
    base.fit(X.iloc[:-n_cal].fillna(0), y.iloc[:-n_cal])
    final = CalibratedClassifierCV(estimator=base, method="isotonic", cv="prefit")
    final.fit(X.iloc[-n_cal:].fillna(0), y.iloc[-n_cal:])

    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(MODEL_PATH, "wb") as f:
        pickle.dump({"model": final, "features": list(X.columns),
                     "auc": float(np.mean(aucs)), "brier": float(np.mean(briers))}, f)
    print(f"Saved to {MODEL_PATH}")


def _train_on_synthetic() -> None:
    """Smoke-test path: fits on synthetic data with no predictive content.
    Only verifies the training pipeline runs end-to-end."""
    from sklearn.calibration import CalibratedClassifierCV
    from sklearn.ensemble import GradientBoostingClassifier

    rng = np.random.default_rng(0)
    n = 1000
    rets = rng.normal(0, 0.012, n)
    idx = pd.date_range("2021-01-01", periods=n, freq="B")
    close = pd.Series(100 * np.exp(np.cumsum(rets)), index=idx)
    high = close * (1 + rng.uniform(0, 0.01, n))
    low = close * (1 - rng.uniform(0, 0.01, n))
    df = pd.DataFrame({"Open": close, "High": high, "Low": low,
                       "Close": close, "Volume": rng.integers(1e5, 1e7, n).astype(float)})
    feat = build_features(df)
    fwd = (close.shift(-FORWARD_DAYS) / close - 1) * 100
    combined = feat.join(fwd.rename("fwd")).dropna().iloc[:-FORWARD_DAYS]
    X = combined.drop("fwd", axis=1).fillna(0)
    y = (combined["fwd"] > LABEL_THRESHOLD).astype(int)

    base = GradientBoostingClassifier(n_estimators=50, max_depth=2, random_state=42)
    n_cal = max(50, len(X) // 5)
    base.fit(X.iloc[:-n_cal], y.iloc[:-n_cal])
    final = CalibratedClassifierCV(estimator=base, method="isotonic", cv="prefit")
    final.fit(X.iloc[-n_cal:], y.iloc[-n_cal:])

    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(MODEL_PATH, "wb") as f:
        pickle.dump({"model": final, "features": list(X.columns), "auc": 0.5, "brier": 0.25,
                     "synthetic": True}, f)
    print(f"Saved synthetic smoke-test model to {MODEL_PATH}")


# ── Predict ───────────────────────────────────────────────────────────────────

def predict_proba(df: pd.DataFrame) -> Optional[float]:
    """Return calibrated P(forward_5d_return > 1.5%) for the latest bar.

    Returns None if the model is not trained — callers fall back to the
    legacy 0-10 technical_score.
    """
    if not MODEL_PATH.exists():
        return None
    with open(MODEL_PATH, "rb") as f:
        saved = pickle.load(f)
    feat = build_features(df)
    latest = feat.iloc[[-1]][saved["features"]].fillna(0)
    return float(saved["model"].predict_proba(latest)[0][1])


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "train"
    if cmd == "train":
        train()
    elif cmd == "predict" and len(sys.argv) > 2:
        sym = sys.argv[2].upper()
        path = Path("stocks") / sym / "price_history.parquet"
        if not path.exists():
            print(f"No price data for {sym}"); sys.exit(1)
        df = pd.read_parquet(path).sort_index()
        df.index = pd.to_datetime(df.index, utc=True).tz_localize(None)
        p = predict_proba(df)
        print(f"{sym}: learned_tech_score_proba = {p:.4f}" if p else f"{sym}: model not trained")
    else:
        print("Usage: python models/learned_tech_score.py train|predict SYMBOL")
