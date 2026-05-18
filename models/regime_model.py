"""
Probabilistic regime classifier (Stage 3b).

Replaces the hard rule-based classification in RegimeAgent with a
GaussianMixture model trained on (return_20d, volatility_20d, vix) features.

Output: a soft probability vector {trending_bull, trending_bear,
high_volatility, ranging} instead of a single hard label. Downstream code
can weight signals by P(bull) rather than if regime == 'bull'.

Usage:
    python models/regime_model.py train    # train and save from local KB data
    python models/regime_model.py predict  # predict current regime

The model trains on historical NIFTY data from stocks/NIFTY/price_history.parquet
if it exists, else synthesises data to allow a smoke-test without real prices.
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

MODEL_PATH = Path("models") / "regime_gmm.pkl"
N_COMPONENTS = 4
LABEL_NAMES = ["trending_bull", "trending_bear", "high_volatility", "ranging"]

# ── Feature builder ───────────────────────────────────────────────────────────

def build_regime_features(close: pd.Series, vix: Optional[pd.Series] = None,
                          lookback: int = 20) -> pd.DataFrame:
    """Return a DataFrame with (return_20d, volatility_20d, vix_level) per bar.

    These three features span the signal space the rule-based classifier uses,
    letting the GMM learn the natural cluster geometry rather than imposing
    hand-tuned thresholds.
    """
    ret_20d = (close / close.shift(lookback) - 1) * 100
    vol_20d = close.pct_change().rolling(lookback).std() * np.sqrt(252) * 100

    feat = pd.DataFrame({"ret_20d": ret_20d, "vol_20d": vol_20d}, index=close.index)

    if vix is not None:
        feat["vix"] = vix.reindex(close.index, method="ffill").fillna(16.0)
    else:
        feat["vix"] = 16.0   # neutral fallback when VIX data absent

    return feat.dropna()


# ── Label assignment for training (uses the rule-based classifier) ────────────

def rule_label(row: pd.Series) -> int:
    """Deterministic label used to seed GMM component ordering, not to train."""
    from agents.regime_agent import STRATEGY_ADJUSTMENTS
    ret, vol, vix = row["ret_20d"], row["vol_20d"], row["vix"]
    # Mirrors RegimeAgent thresholds exactly.
    if ret > 2 and vol < 20:
        return LABEL_NAMES.index("trending_bull")
    elif ret < -2 and vol < 20:
        return LABEL_NAMES.index("trending_bear")
    elif vol >= 20 or vix > 20:
        return LABEL_NAMES.index("high_volatility")
    else:
        return LABEL_NAMES.index("ranging")


# ── Train ─────────────────────────────────────────────────────────────────────

def train(nifty_close: Optional[pd.Series] = None,
          vix: Optional[pd.Series] = None) -> None:
    """Fit a 4-component GaussianMixture and save to MODEL_PATH.

    If nifty_close is None, falls back to any price_history.parquet files in
    stocks/ (pooling across symbols for a richer covariance estimate).
    """
    from sklearn.mixture import GaussianMixture
    from sklearn.preprocessing import StandardScaler

    close_series: list[pd.Series] = []

    if nifty_close is not None:
        close_series.append(nifty_close)
    else:
        for p in Path("stocks").glob("*/price_history.parquet"):
            try:
                df = pd.read_parquet(p).sort_index()
                df.index = pd.to_datetime(df.index, utc=True).tz_localize(None)
                close_series.append(df["Close"])
            except Exception:
                pass

    if not close_series:
        # Synthetic fallback for local smoke-test.
        rng = np.random.default_rng(0)
        n = 1000
        rets = rng.normal(0, 0.012, n)
        idx = pd.date_range("2020-01-01", periods=n, freq="B")
        close_series.append(pd.Series(100 * np.exp(np.cumsum(rets)), index=idx))
        print("No real price data found — training on synthetic data (smoke test only)")

    all_feat: list[pd.DataFrame] = []
    for c in close_series:
        feat = build_regime_features(c, vix=vix)
        all_feat.append(feat)

    X = pd.concat(all_feat)[["ret_20d", "vol_20d", "vix"]].values
    print(f"Training GMM on {len(X):,} samples, {N_COMPONENTS} components")

    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)

    gmm = GaussianMixture(n_components=N_COMPONENTS, covariance_type="full",
                          random_state=42, n_init=5, max_iter=200)
    gmm.fit(X_scaled)

    # Re-order components by their mean return so labels are consistent.
    # Component with highest mean ret_20d → trending_bull, lowest → trending_bear.
    mean_rets = gmm.means_[:, 0]  # ret_20d column (post-scaling, but ordering preserved)
    mean_vols = gmm.means_[:, 1]  # vol_20d
    order = [None] * N_COMPONENTS

    sort_by_ret = np.argsort(mean_rets)
    # Highest return → bull, lowest → bear
    order[LABEL_NAMES.index("trending_bull")] = sort_by_ret[-1]
    order[LABEL_NAMES.index("trending_bear")] = sort_by_ret[0]
    # Among the remaining, highest vol → high_volatility, other → ranging
    remaining = [i for i in sort_by_ret[1:-1] if i not in order]
    remaining_vols = [(i, mean_vols[i]) for i in remaining]
    remaining_vols.sort(key=lambda x: x[1], reverse=True)
    order[LABEL_NAMES.index("high_volatility")] = remaining_vols[0][0]
    order[LABEL_NAMES.index("ranging")] = remaining_vols[1][0] if len(remaining_vols) > 1 else remaining[0]

    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(MODEL_PATH, "wb") as f:
        pickle.dump({"gmm": gmm, "scaler": scaler, "label_order": order,
                     "feature_cols": ["ret_20d", "vol_20d", "vix"]}, f)
    print(f"Saved to {MODEL_PATH}")


# ── Predict ───────────────────────────────────────────────────────────────────

def predict_proba(ret_20d: float, vol_20d: float, vix: float = 16.0
                  ) -> Optional[dict[str, float]]:
    """Return a dict {regime_name: probability} or None if model not available.

    Probabilities sum to 1.0. Callers should propagate None gracefully and
    fall back to the rule-based classifier.
    """
    if not MODEL_PATH.exists():
        return None
    with open(MODEL_PATH, "rb") as f:
        saved = pickle.load(f)

    X = np.array([[ret_20d, vol_20d, vix]])
    X_scaled = saved["scaler"].transform(X)
    raw_proba = saved["gmm"].predict_proba(X_scaled)[0]

    order = saved["label_order"]
    result: dict[str, float] = {}
    for label_idx, comp_idx in enumerate(order):
        if comp_idx is None:
            continue
        result[LABEL_NAMES[label_idx]] = float(raw_proba[comp_idx])

    # Normalise (order mapping may have None slots on malformed saves).
    total = sum(result.values())
    if total > 0:
        result = {k: round(v / total, 4) for k, v in result.items()}
    return result


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "train"
    if cmd == "train":
        train()
    elif cmd == "predict":
        proba = predict_proba(ret_20d=3.5, vol_20d=14.0, vix=13.0)
        print(proba)
    else:
        print("Usage: python models/regime_model.py train|predict")
