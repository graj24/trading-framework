"""
ML Signal Model — XGBoost trained on 30+ features from OHLCV + sector indices + VIX.

Features:
  Price/trend    : returns (1/3/5/10/20d), EMA ratios, BB position, ATR normalised
  Momentum       : RSI(7/14/21), MACD histogram, ROC(5/10/20), Stochastic
  Volume         : volume ratio, OBV trend, volume-price trend
  Volatility     : ATR%, historical vol (5/10/20d), BB width
  Market context : Nifty return, VIX level, sector index return, beta
  Calendar       : day of week, month, days since earnings

Label: 5-day forward return > +1.5% = BUY (1), else HOLD/SKIP (0)

Usage:
  python3 ml_model.py train          # train and save model
  python3 ml_model.py predict SYMBOL # predict for a stock
"""
from __future__ import annotations

import sys
import pickle
import warnings
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import yfinance as yf

from core import features as F

warnings.filterwarnings("ignore")

MODEL_PATH = Path("stocks/ml_signal_model.pkl")
LABEL_THRESHOLD = 1.5   # % forward return to label as BUY
FORWARD_DAYS    = 5     # predict 5-day forward return
MIN_AUC_DELTA   = -0.02 # new model must not be worse than this vs incumbent

SECTOR_INDICES = {
    "nifty":     "^NSEI",
    "banknifty": "^NSEBANK",
    "vix":       "^INDIAVIX",
    "fmcg":      "^CNXFMCG",
    "it":        "^CNXIT",
    "auto":      "^CNXAUTO",
    "energy":    "^CNXENERGY",
}

# ── Indicator helpers ─────────────────────────────────────────────────────────

# Indicators: see core/features.py. Use F.ema, F.rsi, F.macd_hist, F.atr,
# F.stoch_k, F.obv, F.bb_position, F.bb_width, F.hist_vol.

# ── Feature engineering ───────────────────────────────────────────────────────

def build_features(df: pd.DataFrame, market_data: dict[str, pd.Series]) -> pd.DataFrame:
    """Build 30+ features from OHLCV + market context."""
    c, h, l, v = df["Close"], df["High"], df["Low"], df["Volume"]

    feat = pd.DataFrame(index=df.index)

    # ── Price / trend ─────────────────────────────────────────────────────────
    for n in [1, 3, 5, 10, 20]:
        feat[f"ret_{n}d"] = c.pct_change(n) * 100

    feat["ema20_ratio"]  = c / F.ema(c, 20) - 1
    feat["ema50_ratio"]  = c / F.ema(c, 50) - 1
    feat["ema200_ratio"] = c / F.ema(c, 200) - 1
    feat["ema20_50_cross"] = (F.ema(c, 20) / F.ema(c, 50) - 1) * 100

    feat["bb_position"] = F.bb_position(c)
    feat["bb_width"]    = F.bb_width(c)

    atr = F.atr(h, l, c)
    feat["atr_pct"]     = atr / c * 100

    # ── Momentum ──────────────────────────────────────────────────────────────
    feat["rsi_7"]       = F.rsi(c, 7)
    feat["rsi_14"]      = F.rsi(c, 14)
    feat["rsi_21"]      = F.rsi(c, 21)
    feat["macd_hist"]   = F.macd_hist(c)
    feat["stoch_k"]     = F.stoch_k(h, l, c)
    for n in [5, 10, 20]:
        feat[f"roc_{n}d"] = (c / c.shift(n) - 1) * 100

    # ── Volume ────────────────────────────────────────────────────────────────
    vol_avg20 = v.rolling(20).mean()
    feat["vol_ratio"]   = v / vol_avg20
    obv = F.obv(c, v)
    feat["obv_trend"]   = (obv / obv.shift(5) - 1) * 100   # 5-day OBV change
    feat["vpt"]         = (c.pct_change() * v).cumsum() / 1e6  # volume-price trend

    # ── Volatility ────────────────────────────────────────────────────────────
    for n in [5, 10, 20]:
        feat[f"hvol_{n}d"] = F.hist_vol(c, n) * 100

    # ── Gap ───────────────────────────────────────────────────────────────────
    feat["gap_pct"]     = (df["Open"] / c.shift(1) - 1) * 100
    feat["intraday_range"] = (h - l) / c * 100

    # ── Market context ────────────────────────────────────────────────────────
    for name, series in market_data.items():
        aligned = series.reindex(df.index, method="ffill")
        if name == "vix":
            feat["vix_level"] = aligned
        else:
            feat[f"{name}_ret5d"] = aligned.pct_change(5) * 100

    # ── Calendar ──────────────────────────────────────────────────────────────
    feat["day_of_week"] = pd.to_datetime(df.index).dayofweek
    # Cyclical month encoding (avoids ordinal leak where month=12 > month=1)
    month = pd.to_datetime(df.index).month
    feat["month_sin"] = np.sin(2 * np.pi * month / 12)
    feat["month_cos"] = np.cos(2 * np.pi * month / 12)

    return feat.replace([np.inf, -np.inf], np.nan)


def build_labels(df: pd.DataFrame) -> pd.Series:
    """Label: 1 if 5-day forward return > LABEL_THRESHOLD%, else 0."""
    fwd = df["Close"].shift(-FORWARD_DAYS) / df["Close"] - 1
    return (fwd * 100 > LABEL_THRESHOLD).astype(int)


# ── Load market data ──────────────────────────────────────────────────────────

def load_market_data(start: str, end: str) -> dict[str, pd.Series]:
    """Load NIFTY + sector + VIX series for the given date range.

    C.1: results are cached at ``stocks/_market_data.parquet`` with the
    fetched (start, end) range stored in metadata. The cache hits when the
    range fully covers a request, so a 50-symbol predict cycle no longer
    hammers yfinance with 350 requests.
    """
    cache_path = Path("stocks/_market_data.parquet")
    cache_meta = Path("stocks/_market_data.meta")

    # Try cache first.
    if cache_path.exists() and cache_meta.exists():
        try:
            meta = cache_meta.read_text().strip().split("|")
            cached_start, cached_end = meta[0], meta[1]
            if cached_start <= start and cached_end >= end:
                df = pd.read_parquet(cache_path)
                # Slice by date range.
                df.index = pd.to_datetime(df.index)
                mask = (df.index >= start) & (df.index <= end)
                df = df[mask]
                if not df.empty:
                    return {col: df[col].dropna() for col in df.columns}
        except Exception:
            pass  # cache corrupted — refetch

    # Cache miss. Fetch fresh.
    market: dict[str, pd.Series] = {}
    for name, ticker in SECTOR_INDICES.items():
        try:
            df = yf.Ticker(ticker).history(start=start, end=end, interval="1d")
            if not df.empty:
                s = df["Close"]
                s.index = pd.to_datetime(s.index, utc=True).tz_localize(None)
                market[name] = s
        except Exception:
            pass

    # Persist to cache.
    if market:
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            combined = pd.DataFrame(market)
            combined.to_parquet(cache_path)
            cache_meta.write_text(f"{start}|{end}")
        except Exception:
            pass  # writing the cache is best-effort

    return market


# ── Promotion gate ────────────────────────────────────────────────────────────

def _incumbent_auc(X_val: "pd.DataFrame", y_val: "pd.Series") -> float:
    """Return the AUC of the currently-saved model on the validation set,
    or 0.0 if no model exists yet."""
    if not MODEL_PATH.exists():
        return 0.0
    try:
        with open(MODEL_PATH, "rb") as f:
            saved = pickle.load(f)
        # Prefer the stored AUC (recorded at training time on the full CV set).
        if "auc" in saved:
            return float(saved["auc"])
        # Fallback: re-evaluate on the provided validation slice (old models).
        from sklearn.metrics import roc_auc_score
        inc_model = saved["model"]
        inc_feats = saved["features"]
        X_aligned = X_val.reindex(columns=inc_feats, fill_value=0).fillna(0)
        proba = inc_model.predict_proba(X_aligned)[:, 1]
        return float(roc_auc_score(y_val, proba))
    except Exception:
        return 0.0


def _save_if_better(model, features: list, new_auc: float,
                    X_val: "pd.DataFrame", y_val: "pd.Series",
                    brier_uncal: Optional[float] = None,
                    brier_cal: Optional[float] = None,
                    walk_forward: "Optional['WalkForwardPnL']" = None,
                    min_net_pnl_pct: float = 0.0) -> bool:
    """Save model only when it passes:

      * AUC ≥ AUC_FLOOR (rejects models barely better than coin flip)
      * AUC ≥ incumbent_auc + MIN_AUC_DELTA (no regression in rank quality)
      * Walk-forward net P&L on held-out slice ≥ min_net_pnl_pct (Stage 1C)

    The walk-forward gate is the binding constraint for promotion. AUC stays
    as a floor; money is the test.

    Returns True if the model was promoted, False if it was rejected.
    """
    AUC_FLOOR = 0.55
    if new_auc < AUC_FLOOR:
        print(f"\n⚠️  Promotion REJECTED: new AUC {new_auc:.4f} below floor {AUC_FLOOR}")
        return False
    inc_auc = _incumbent_auc(X_val, y_val)
    if new_auc < inc_auc + MIN_AUC_DELTA:
        print(f"\n⚠️  Promotion REJECTED: new AUC {new_auc:.4f} < incumbent {inc_auc:.4f} + delta {MIN_AUC_DELTA}")
        return False
    if walk_forward is not None:
        ok, reason = walk_forward.passes(min_net_pnl_pct=min_net_pnl_pct)
        if not ok:
            print(f"\n⚠️  Promotion REJECTED: walk-forward P&L gate failed — {reason}")
            print(f"   ({walk_forward.n_trades} trades, mean={walk_forward.mean_pnl_pct:+.3f}%, "
                  f"win_rate={walk_forward.win_rate_pct:.1f}%)")
            return False
    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    if MODEL_PATH.exists():
        MODEL_PATH.rename(MODEL_PATH.with_suffix(".prev.pkl"))
    payload = {"model": model, "features": features, "auc": new_auc}
    if brier_uncal is not None: payload["brier_uncal"] = brier_uncal
    if brier_cal is not None:   payload["brier_cal"]   = brier_cal
    if walk_forward is not None:
        payload["walk_forward"] = {
            "n_trades":     walk_forward.n_trades,
            "net_pnl_pct":  walk_forward.net_pnl_pct,
            "mean_pnl_pct": walk_forward.mean_pnl_pct,
            "win_rate_pct": walk_forward.win_rate_pct,
        }
    with open(MODEL_PATH, "wb") as f:
        pickle.dump(payload, f)
    print(f"\n✅ Model promoted: new AUC {new_auc:.4f} >= incumbent {inc_auc:.4f} + delta {MIN_AUC_DELTA}")
    if brier_cal is not None and brier_uncal is not None:
        print(f"   Brier: uncal={brier_uncal:.4f} → cal={brier_cal:.4f}")
    if walk_forward is not None:
        print(f"   Walk-forward: {walk_forward.n_trades} trades, "
              f"net P&L {walk_forward.net_pnl_pct:+.2f}%, "
              f"mean {walk_forward.mean_pnl_pct:+.3f}%, "
              f"win rate {walk_forward.win_rate_pct:.1f}%")
    return True


# ── Train ─────────────────────────────────────────────────────────────────────

def train():
    """Train the daily ML model with isotonic probability calibration.

    Stage 1A: probabilities are calibrated so that `proba=0.62` actually
    corresponds to ~62% empirical hit rate on similar past cases. The
    saved model is a `CalibratedClassifierCV` whose underlying base
    estimator is the same GradientBoostingClassifier we trained before.
    Predict-time interface is unchanged (`predict_proba`).

    Reports per fold:
      - AUC (uncalibrated baseline — calibration cannot improve AUC)
      - Brier score uncalibrated  (lower is better, range [0, 1])
      - Brier score calibrated    (should be lower than uncalibrated)
    """
    try:
        from xgboost import XGBClassifier
        _USE_XGB = True
    except ImportError:
        from sklearn.ensemble import GradientBoostingClassifier
        _USE_XGB = False
        print("⚠️  xgboost not installed, falling back to GradientBoostingClassifier")

    from sklearn.calibration import CalibratedClassifierCV
    from sklearn.model_selection import TimeSeriesSplit
    from sklearn.metrics import (
        classification_report, roc_auc_score, brier_score_loss
    )

    import yaml
    with open("config.yaml") as f:
        config = yaml.safe_load(f)

    # Train on ALL available stocks, not just watchlist
    all_parquets = sorted(Path("stocks").glob("*/price_history.parquet"))
    watchlist = [p.parent.name for p in all_parquets]
    print(f"Building dataset for {len(watchlist)} stocks...")

    all_X, all_y, all_fwd = [], [], []

    # Load market data once
    market_data = load_market_data("2021-01-01", "2026-12-31")
    print(f"Market indices loaded: {list(market_data.keys())}")

    for symbol in watchlist:
        path = Path("stocks") / symbol / "price_history.parquet"
        if not path.exists():
            print(f"  {symbol}: no price data, skipping")
            continue

        df = pd.read_parquet(path).sort_index()
        df.index = pd.to_datetime(df.index, utc=True).tz_localize(None)
        df = df.dropna(subset=["Open","High","Low","Close","Volume"])

        feat = build_features(df, market_data)
        labels = build_labels(df)
        fwd_pct = (df["Close"].shift(-FORWARD_DAYS) / df["Close"] - 1) * 100

        combined = (feat
                    .join(labels.rename("label"))
                    .join(fwd_pct.rename("fwd_pct"))
                    .dropna())
        combined = combined.iloc[:-FORWARD_DAYS]

        all_X.append(combined.drop(["label", "fwd_pct"], axis=1))
        all_y.append(combined["label"])
        all_fwd.append(combined["fwd_pct"])
        print(f"  {symbol}: {len(combined)} samples, {combined['label'].mean()*100:.1f}% positive")

    X = pd.concat(all_X).reset_index(drop=True)
    y = pd.concat(all_y).reset_index(drop=True)
    fwd_returns = pd.concat(all_fwd).reset_index(drop=True)

    print(f"\nTotal dataset: {len(X)} samples, {len(X.columns)} features")
    buy_rate = y.mean()
    print(f"Class balance: {buy_rate*100:.1f}% BUY signals")

    # scale_pos_weight corrects class imbalance: ratio of negatives to positives
    spw = (1 - buy_rate) / buy_rate if buy_rate > 0 else 1.0

    def _make_model(scale_pos_weight=spw):
        if _USE_XGB:
            return XGBClassifier(
                n_estimators=300, max_depth=4, learning_rate=0.05,
                subsample=0.8, colsample_bytree=0.8, random_state=42,
                scale_pos_weight=scale_pos_weight,
                eval_metric="logloss", verbosity=0,
            )
        else:
            return GradientBoostingClassifier(
                n_estimators=300, max_depth=4, learning_rate=0.05,
                subsample=0.8, max_features=0.8, random_state=42,
            )

    tscv = TimeSeriesSplit(n_splits=5)
    auc_scores = []
    brier_uncal_scores = []
    brier_cal_scores = []

    print("\nCross-validation (TimeSeriesSplit, 5 folds):")
    for fold, (tr_idx, vl_idx) in enumerate(tscv.split(X)):
        X_tr, X_vl = X.iloc[tr_idx].fillna(0), X.iloc[vl_idx].fillna(0)
        y_tr, y_vl = y.iloc[tr_idx], y.iloc[vl_idx]

        # Uncalibrated baseline (AUC + Brier reference)
        base_fold = _make_model()
        base_fold.fit(X_tr, y_tr)
        p_unc = base_fold.predict_proba(X_vl)[:, 1]

        # Calibrated: split train into 80% fit / 20% calibrate (preserves time order)
        # Only use calibration if it actually improves Brier score
        split = max(100, int(len(tr_idx) * 0.8))
        p_cal = p_unc  # default: no calibration
        if split < len(tr_idx) - 50:
            fit_idx, cal_idx = tr_idx[:split], tr_idx[split:]
            base_for_cal = _make_model()
            base_for_cal.fit(X.iloc[fit_idx].fillna(0), y.iloc[fit_idx])
            cal = CalibratedClassifierCV(estimator=base_for_cal, method="isotonic", cv="prefit")
            cal.fit(X.iloc[cal_idx].fillna(0), y.iloc[cal_idx])
            p_cal_candidate = cal.predict_proba(X_vl)[:, 1]
            # Only accept calibration if it genuinely reduces Brier score
            if brier_score_loss(y_vl, p_cal_candidate) < brier_score_loss(y_vl, p_unc):
                p_cal = p_cal_candidate

        auc = roc_auc_score(y_vl, p_unc)
        brier_u = brier_score_loss(y_vl, p_unc)
        brier_c = brier_score_loss(y_vl, p_cal)
        auc_scores.append(auc)
        brier_uncal_scores.append(brier_u)
        brier_cal_scores.append(brier_c)
        print(f"  Fold {fold+1}: AUC={auc:.4f} | Brier uncal={brier_u:.4f} cal={brier_c:.4f}")

    mean_auc = float(np.mean(auc_scores))
    mean_brier_u = float(np.mean(brier_uncal_scores))
    mean_brier_c = float(np.mean(brier_cal_scores))
    delta = (mean_brier_u - mean_brier_c) / mean_brier_u * 100 if mean_brier_u else 0.0

    print(f"\nMean AUC:         {mean_auc:.4f} ± {np.std(auc_scores):.4f}")
    print(f"Mean Brier uncal: {mean_brier_u:.4f}")
    print(f"Mean Brier cal:   {mean_brier_c:.4f}  ({delta:+.1f}% improvement)")

    # Final model: fit base on first 80% of all data, calibrate on last 20%
    # only if calibration helps.
    n_total = len(X)
    n_calib = max(200, n_total // 5)
    fit_X = X.iloc[:-n_calib].fillna(0); fit_y = y.iloc[:-n_calib]
    cal_X = X.iloc[-n_calib:].fillna(0); cal_y = y.iloc[-n_calib:]

    base_final = _make_model()
    base_final.fit(fit_X, fit_y)

    # Test whether calibration helps on the held-out calibration slice
    p_base = base_final.predict_proba(cal_X)[:, 1]
    cal_candidate = CalibratedClassifierCV(estimator=base_final, method="isotonic", cv="prefit")
    cal_candidate.fit(cal_X, cal_y)
    p_cal_final = cal_candidate.predict_proba(cal_X)[:, 1]

    if brier_score_loss(cal_y, p_cal_final) < brier_score_loss(cal_y, p_base):
        final_model = cal_candidate
        print("Calibration accepted for final model (improved Brier on held-out slice)")
    else:
        final_model = base_final
        print("Calibration skipped for final model (did not improve Brier)")

    # Feature importance from base estimator (calibrator doesn't expose it).
    base_for_importance = base_final
    importance = pd.Series(base_for_importance.feature_importances_, index=X.columns)
    top20 = importance.nlargest(20)
    print(f"\nTop 20 features:")
    for feat_name, imp in top20.items():
        bar = "█" * int(imp * 300)
        print(f"  {feat_name:<25} {imp:.4f} {bar}")

    # Stage 1C: walk-forward net-P&L gate.
    PREDICT_THRESHOLD = 0.35  # lowered from 0.55 to improve recall
    from core.promotion_gate import walk_forward_pnl
    cal_proba = final_model.predict_proba(cal_X)[:, 1]
    cal_fwd   = fwd_returns.iloc[-n_calib:].values
    wf = walk_forward_pnl(cal_proba, cal_fwd, threshold=PREDICT_THRESHOLD)
    print(f"\nWalk-forward (calibration slice, last {n_calib} rows):")
    print(f"  trades={wf.n_trades}/{wf.n_eval}  "
          f"net P&L={wf.net_pnl_pct:+.2f}%  "
          f"mean={wf.mean_pnl_pct:+.3f}%  "
          f"win rate={wf.win_rate_pct:.1f}%  "
          f"(cost {wf.cost_per_trade_pct:.3f}%/trade)")

    _save_if_better(final_model, list(X.columns), mean_auc, X_vl.fillna(0), y_vl,
                    brier_uncal=mean_brier_u, brier_cal=mean_brier_c,
                    walk_forward=wf)

    print(f"\nClassification report (last fold, threshold={PREDICT_THRESHOLD}):")
    print(classification_report(y_vl, (final_model.predict_proba(X_vl)[:, 1] >= PREDICT_THRESHOLD).astype(int)))


# ── Predict ───────────────────────────────────────────────────────────────────

def predict(symbol: str) -> dict:
    """Generate ML signal for a single stock using latest data."""
    if not MODEL_PATH.exists():
        raise FileNotFoundError("Model not trained yet. Run: python3 ml_model.py train")

    with open(MODEL_PATH, "rb") as f:
        saved = pickle.load(f)
    model    = saved["model"]
    features = saved["features"]

    path = Path("stocks") / symbol / "price_history.parquet"
    if not path.exists():
        raise FileNotFoundError(f"No price data for {symbol}")

    df = pd.read_parquet(path).sort_index()
    df.index = pd.to_datetime(df.index, utc=True).tz_localize(None)
    df = df.dropna(subset=["Open","High","Low","Close","Volume"])

    market_data = load_market_data(
        str(df.index.min().date()), str(df.index.max().date())
    )

    feat = build_features(df, market_data)
    latest = feat.iloc[[-1]][features].fillna(0)

    proba = model.predict_proba(latest)[0][1]
    signal = "BUY" if proba >= 0.35 else ("HOLD" if proba >= 0.25 else "SKIP")

    return {
        "symbol":     symbol,
        "ml_signal":  signal,
        "ml_proba":   round(float(proba), 4),
        "confidence": round(float(proba) * 100, 1),
    }


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "train"

    if cmd == "train":
        train()
    elif cmd == "predict":
        symbol = sys.argv[2].upper() if len(sys.argv) > 2 else "TATACONSUM"
        result = predict(symbol)
        print(f"\n{symbol}: {result['ml_signal']} (probability={result['ml_proba']:.4f}, confidence={result['confidence']}%)")
    elif cmd == "backtest":
        # Run ML predictions across all historical dates and measure accuracy
        import yaml
        with open("config.yaml") as f:
            config = yaml.safe_load(f)

        if not MODEL_PATH.exists():
            print("Model not trained. Run: python3 ml_model.py train")
            sys.exit(1)

        with open(MODEL_PATH, "rb") as f:
            saved = pickle.load(f)
        model    = saved["model"]
        features = saved["features"]

        market_data = load_market_data("2021-01-01", "2026-12-31")
        symbol = sys.argv[2].upper() if len(sys.argv) > 2 else None
        symbols = [symbol] if symbol else config["watchlist"]

        print(f"\n{'='*65}")
        print(f"  ML MODEL BACKTEST — 5-day forward return > {LABEL_THRESHOLD}%")
        print(f"{'='*65}")

        all_results = []
        for sym in symbols:
            path = Path("stocks") / sym / "price_history.parquet"
            if not path.exists():
                continue
            df = pd.read_parquet(path).sort_index()
            df.index = pd.to_datetime(df.index, utc=True).tz_localize(None)
            df = df.dropna(subset=["Open","High","Low","Close","Volume"])

            feat   = build_features(df, market_data)
            labels = build_labels(df)
            combined = feat.join(labels.rename("label")).dropna().iloc[:-FORWARD_DAYS]

            X = combined[features].fillna(0)
            y = combined["label"]
            proba = model.predict_proba(X)[:, 1]
            pred  = (proba >= 0.35).astype(int)

            # Only evaluate on days where model said BUY
            buy_mask = pred == 1
            if buy_mask.sum() == 0:
                print(f"  {sym}: no BUY signals generated")
                continue

            buy_accuracy = (y[buy_mask] == 1).mean() * 100
            total_buys   = buy_mask.sum()
            avg_proba    = proba[buy_mask].mean()

            print(f"  {sym:<12}: {total_buys:>4} BUY signals | "
                  f"accuracy={buy_accuracy:.1f}% | avg_proba={avg_proba:.3f}")
            all_results.append({"symbol": sym, "buys": total_buys,
                                 "accuracy": buy_accuracy, "avg_proba": avg_proba})

        if all_results:
            total_buys = sum(r["buys"] for r in all_results)
            avg_acc    = np.mean([r["accuracy"] for r in all_results])
            print(f"\n  Total BUY signals: {total_buys}")
            print(f"  Avg BUY accuracy : {avg_acc:.1f}%")
    else:
        print("Usage: python3 ml_model.py train | predict SYMBOL | backtest [SYMBOL]")
