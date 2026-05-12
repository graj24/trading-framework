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

import numpy as np
import pandas as pd
import yfinance as yf

warnings.filterwarnings("ignore")

MODEL_PATH = Path("stocks/ml_signal_model.pkl")
LABEL_THRESHOLD = 1.5   # % forward return to label as BUY
FORWARD_DAYS    = 5     # predict 5-day forward return

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

def _ema(s, n): return s.ewm(span=n, adjust=False).mean()

def _rsi(s, n=14):
    d = s.diff()
    g = d.clip(lower=0).ewm(alpha=1/n, min_periods=n).mean()
    l = (-d.clip(upper=0)).ewm(alpha=1/n, min_periods=n).mean()
    return 100 - 100 / (1 + g / l.replace(0, np.nan))

def _macd_hist(s):
    m = _ema(s, 12) - _ema(s, 26)
    return m - _ema(m, 9)

def _atr(h, l, c, n=14):
    tr = pd.concat([h-l, (h-c.shift()).abs(), (l-c.shift()).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1/n, min_periods=n).mean()

def _stoch(h, l, c, k=14):
    lo = l.rolling(k).min()
    hi = h.rolling(k).max()
    return (c - lo) / (hi - lo + 1e-9) * 100

def _obv(c, v):
    return (np.sign(c.diff()).fillna(0) * v).cumsum()

def _bb_position(c, n=20):
    sma = c.rolling(n).mean()
    std = c.rolling(n).std()
    return (c - (sma - 2*std)) / (4*std + 1e-9)   # 0=lower band, 1=upper band

def _bb_width(c, n=20):
    sma = c.rolling(n).mean()
    std = c.rolling(n).std()
    return (4 * std) / (sma + 1e-9)

def _hist_vol(c, n):
    return c.pct_change().rolling(n).std() * np.sqrt(252)

# ── Feature engineering ───────────────────────────────────────────────────────

def build_features(df: pd.DataFrame, market_data: dict[str, pd.Series]) -> pd.DataFrame:
    """Build 30+ features from OHLCV + market context."""
    c, h, l, v = df["Close"], df["High"], df["Low"], df["Volume"]

    feat = pd.DataFrame(index=df.index)

    # ── Price / trend ─────────────────────────────────────────────────────────
    for n in [1, 3, 5, 10, 20]:
        feat[f"ret_{n}d"] = c.pct_change(n) * 100

    feat["ema20_ratio"]  = c / _ema(c, 20) - 1
    feat["ema50_ratio"]  = c / _ema(c, 50) - 1
    feat["ema200_ratio"] = c / _ema(c, 200) - 1
    feat["ema20_50_cross"] = (_ema(c, 20) / _ema(c, 50) - 1) * 100

    feat["bb_position"] = _bb_position(c)
    feat["bb_width"]    = _bb_width(c)

    atr = _atr(h, l, c)
    feat["atr_pct"]     = atr / c * 100

    # ── Momentum ──────────────────────────────────────────────────────────────
    feat["rsi_7"]       = _rsi(c, 7)
    feat["rsi_14"]      = _rsi(c, 14)
    feat["rsi_21"]      = _rsi(c, 21)
    feat["macd_hist"]   = _macd_hist(c)
    feat["stoch_k"]     = _stoch(h, l, c)
    for n in [5, 10, 20]:
        feat[f"roc_{n}d"] = (c / c.shift(n) - 1) * 100

    # ── Volume ────────────────────────────────────────────────────────────────
    vol_avg20 = v.rolling(20).mean()
    feat["vol_ratio"]   = v / vol_avg20
    obv = _obv(c, v)
    feat["obv_trend"]   = (obv / obv.shift(5) - 1) * 100   # 5-day OBV change
    feat["vpt"]         = (c.pct_change() * v).cumsum() / 1e6  # volume-price trend

    # ── Volatility ────────────────────────────────────────────────────────────
    for n in [5, 10, 20]:
        feat[f"hvol_{n}d"] = _hist_vol(c, n) * 100

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
    feat["month"]       = pd.to_datetime(df.index).month

    return feat.replace([np.inf, -np.inf], np.nan)


def build_labels(df: pd.DataFrame) -> pd.Series:
    """Label: 1 if 5-day forward return > LABEL_THRESHOLD%, else 0."""
    fwd = df["Close"].shift(-FORWARD_DAYS) / df["Close"] - 1
    return (fwd * 100 > LABEL_THRESHOLD).astype(int)


# ── Load market data ──────────────────────────────────────────────────────────

def load_market_data(start: str, end: str) -> dict[str, pd.Series]:
    market = {}
    for name, ticker in SECTOR_INDICES.items():
        try:
            df = yf.Ticker(ticker).history(start=start, end=end, interval="1d")
            if not df.empty:
                s = df["Close"]
                s.index = pd.to_datetime(s.index, utc=True).tz_localize(None)
                market[name] = s
        except Exception:
            pass
    return market


# ── Train ─────────────────────────────────────────────────────────────────────

def train():
    from sklearn.ensemble import GradientBoostingClassifier
    from sklearn.model_selection import TimeSeriesSplit
    from sklearn.metrics import classification_report, roc_auc_score

    import yaml
    with open("config.yaml") as f:
        config = yaml.safe_load(f)
    watchlist = config["watchlist"]

    print(f"Building dataset for {len(watchlist)} stocks...")

    all_X, all_y = [], []

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

        # Align and drop NaN
        combined = feat.join(labels.rename("label")).dropna()
        combined = combined.iloc[:-FORWARD_DAYS]  # remove last rows (no label yet)

        all_X.append(combined.drop("label", axis=1))
        all_y.append(combined["label"])
        print(f"  {symbol}: {len(combined)} samples, {combined['label'].mean()*100:.1f}% positive")

    X = pd.concat(all_X).reset_index(drop=True)
    y = pd.concat(all_y).reset_index(drop=True)

    print(f"\nTotal dataset: {len(X)} samples, {len(X.columns)} features")
    print(f"Class balance: {y.mean()*100:.1f}% BUY signals")

    model = GradientBoostingClassifier(
        n_estimators=300,
        max_depth=4,
        learning_rate=0.05,
        subsample=0.8,
        max_features=0.8,
        random_state=42,
    )

    # Time-series cross-validation
    tscv = TimeSeriesSplit(n_splits=5)
    auc_scores = []

    print("\nCross-validation (TimeSeriesSplit, 5 folds):")
    for fold, (train_idx, val_idx) in enumerate(tscv.split(X)):
        X_tr, X_val = X.iloc[train_idx].fillna(0), X.iloc[val_idx].fillna(0)
        y_tr, y_val = y.iloc[train_idx], y.iloc[val_idx]
        model.fit(X_tr, y_tr)
        proba = model.predict_proba(X_val)[:, 1]
        auc = roc_auc_score(y_val, proba)
        auc_scores.append(auc)
        print(f"  Fold {fold+1}: AUC = {auc:.4f}")

    print(f"\nMean AUC: {np.mean(auc_scores):.4f} ± {np.std(auc_scores):.4f}")

    # Final fit on all data
    model.fit(X.fillna(0), y)

    # Feature importance
    importance = pd.Series(model.feature_importances_, index=X.columns)
    top20 = importance.nlargest(20)
    print(f"\nTop 20 features:")
    for feat_name, imp in top20.items():
        bar = "█" * int(imp * 300)
        print(f"  {feat_name:<25} {imp:.4f} {bar}")

    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(MODEL_PATH, "wb") as f:
        pickle.dump({"model": model, "features": list(X.columns)}, f)
    print(f"\nModel saved to {MODEL_PATH}")

    print(f"\nClassification report (last fold):")
    print(classification_report(y_val, model.predict(X_val.fillna(0))))


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
    signal = "BUY" if proba >= 0.55 else ("HOLD" if proba >= 0.40 else "SKIP")

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
            pred  = (proba >= 0.55).astype(int)

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
