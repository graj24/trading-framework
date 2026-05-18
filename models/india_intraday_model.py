"""
India Intraday ML Model — 1h candles, NSE stocks only.

Features:
  Intraday-aware : hour of day, session (morning/midday/close), minutes to close
  Price/momentum : returns (1/2/3/6h), EMA ratios, MACD histogram, RSI
  Gap            : overnight gap from prev close, gap fill %
  Volume         : volume ratio vs same-hour avg, OBV trend
  Volatility     : ATR%, intraday range, historical vol
  F&O context    : days to monthly expiry (last Thursday), is-expiry-week flag
  Market context : Nifty 1h return, BankNifty 1h return, India VIX

Label: next 3-hour return > +1.0% = BUY (1), else 0

Usage:
  python3 india_intraday_model.py fetch    # fetch 1h data for all Nifty50
  python3 india_intraday_model.py train    # train model
  python3 india_intraday_model.py predict SYMBOL  # predict for latest candle
"""
from __future__ import annotations

import sys
import pickle
import time
import warnings
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional

import numpy as np
import pandas as pd
import yfinance as yf

from core import features as F

warnings.filterwarnings("ignore")

DATA_DIR   = Path(__file__).parent / "stocks_1h"
MODEL_PATH = Path(__file__).parent / "stocks_1h" / "india_intraday_model.pkl"
FORWARD_HOURS  = 3
LABEL_THRESHOLD = 1.0   # % forward return
MIN_AUC_DELTA   = -0.02  # new model must not be worse than this vs incumbent

NSE_OPEN  = 9   # 9:15 AM
NSE_CLOSE = 15  # 3:30 PM

# Canonical NIFTY 50 list lives in core/symbols.py (B.1). Build the yfinance
# ticker form here.
from core.symbols import NIFTY_50, to_yfinance_ticker
NIFTY50_TICKERS = [to_yfinance_ticker(s) for s in NIFTY_50]

# ── Helpers ───────────────────────────────────────────────────────────────────

# Indicators: see core/features.py. Use F.ema, F.rsi, F.atr.

def _fo_expiry_days(index: pd.DatetimeIndex) -> pd.Series:
    """Days until next monthly F&O expiry (last Thursday of month, with
    holiday adjustment).

    B.10: when the last Thursday is an NSE holiday, expiry shifts to the
    prior trading day. Without this fix, ~5–10 expiry weeks per year had
    the wrong distance feature, which polluted the trained intraday model.
    """
    from core.holidays import is_trading_day, previous_trading_day

    def last_thursday(dt):
        # Last Thursday of the month
        last = dt.replace(day=28) + timedelta(days=4)
        last = last - timedelta(days=last.weekday())  # Monday of last week
        last += timedelta(days=3)  # Thursday
        if last.month != dt.month:
            last -= timedelta(weeks=1)
        # If that Thursday is a holiday, expiry moves to the previous
        # trading day.
        d = last.date()
        if not is_trading_day(d):
            d = previous_trading_day(d)
            last = last.replace(year=d.year, month=d.month, day=d.day)
        return last

    result = []
    for dt in index:
        expiry = last_thursday(dt)
        days = (expiry.date() - dt.date()).days
        if days < 0:
            # Next month's expiry
            next_month = (dt.replace(day=1) + timedelta(days=32)).replace(day=1)
            expiry = last_thursday(next_month)
            days = (expiry.date() - dt.date()).days
        result.append(days)
    return pd.Series(result, index=index)

# ── Fetch ─────────────────────────────────────────────────────────────────────

def fetch_all():
    DATA_DIR.mkdir(exist_ok=True)
    print(f"Fetching 1h data for {len(NIFTY50_TICKERS)} Nifty50 stocks (~3 years)...")
    ok, failed = 0, 0
    for i, ticker in enumerate(NIFTY50_TICKERS):
        symbol = ticker.replace(".NS","").replace("-","_")
        path = DATA_DIR / f"{symbol}.parquet"
        try:
            df = yf.Ticker(ticker).history(period="730d", interval="1h")
            if df.empty or len(df) < 100:
                print(f"  ❌ {symbol}: insufficient ({len(df)} rows)")
                failed += 1
                continue
            df.index = pd.to_datetime(df.index, utc=True).tz_convert("Asia/Kolkata").tz_localize(None)
            # Keep only market hours
            df = df.between_time("09:00", "15:30")
            df.to_parquet(path)
            print(f"  ✅ {symbol}: {len(df)} rows | {str(df.index.min())[:10]} → {str(df.index.max())[:10]}")
            ok += 1
        except Exception as e:
            print(f"  ❌ {symbol}: {e}")
            failed += 1
        if (i + 1) % 10 == 0:
            time.sleep(1)

    # Also fetch Nifty and BankNifty for market context
    for name, ticker in [("NIFTY_1h", "^NSEI"), ("BANKNIFTY_1h", "^NSEBANK"), ("VIX_1h", "^INDIAVIX")]:
        try:
            df = yf.Ticker(ticker).history(period="730d", interval="1h")
            if not df.empty:
                df.index = pd.to_datetime(df.index, utc=True).tz_convert("Asia/Kolkata").tz_localize(None)
                df.to_parquet(DATA_DIR / f"{name}.parquet")
                print(f"  ✅ {name}: {len(df)} rows")
        except Exception as e:
            print(f"  ❌ {name}: {e}")

    print(f"\nDone: {ok} OK, {failed} failed")

# ── Feature engineering ───────────────────────────────────────────────────────

def build_features(df: pd.DataFrame, nifty: pd.Series, banknifty: pd.Series, vix: pd.Series) -> pd.DataFrame:
    c, h, l, v = df["Close"], df["High"], df["Low"], df["Volume"]
    idx = df.index
    feat = pd.DataFrame(index=idx)

    # ── Time-of-day ───────────────────────────────────────────────────────────
    feat["hour"]            = idx.hour
    feat["mins_to_close"]   = (15 * 60 + 30) - (idx.hour * 60 + idx.minute)
    feat["is_morning"]      = (idx.hour == 9).astype(int)
    feat["is_midday"]       = ((idx.hour >= 11) & (idx.hour <= 13)).astype(int)
    feat["is_power_hour"]   = (idx.hour >= 14).astype(int)

    # ── Intraday returns ──────────────────────────────────────────────────────
    for n in [1, 2, 3, 6]:
        feat[f"ret_{n}h"] = c.pct_change(n) * 100

    # ── Overnight gap (first candle of day vs prev day close) ─────────────────
    day_first = df.groupby(idx.date)["Open"].first()
    day_prev_close = df.groupby(idx.date)["Close"].last().shift(1)
    gap_map = ((day_first - day_prev_close) / day_prev_close * 100).to_dict()
    feat["overnight_gap"] = [gap_map.get(d, 0) for d in idx.date]

    # ── Intraday cumulative return from open ──────────────────────────────────
    day_open_map = df.groupby(idx.date)["Open"].first().to_dict()
    feat["intraday_ret"] = [(c.iloc[i] / day_open_map.get(idx[i].date(), c.iloc[i]) - 1) * 100
                            for i in range(len(c))]

    # ── Momentum ──────────────────────────────────────────────────────────────
    feat["rsi_14"]    = F.rsi(c, 14)
    feat["rsi_6"]     = F.rsi(c, 6)
    feat["macd_hist"] = F.macd_hist(c)
    feat["ema9_ratio"]  = c / F.ema(c, 9) - 1
    feat["ema21_ratio"] = c / F.ema(c, 21) - 1

    # ── Volume ────────────────────────────────────────────────────────────────
    # Volume ratio vs same-hour average — Stage 1B fix.
    # The previous formulation used df.groupby(idx.hour)["Volume"].transform("mean"),
    # which computes a single mean over the entire dataframe for each hour-of-day
    # bucket. At any given training row, that mean includes future bars at the
    # same hour from later days — strict leakage. The expanding form below uses
    # only past bars at the same hour, then `.shift(1)` excludes the current bar.
    # First-seen bar for each hour-of-day has NaN -> 1.0 (neutral).
    hour_avg = df.groupby(idx.hour)["Volume"].transform(
        lambda s: s.expanding(min_periods=1).mean().shift(1)
    )
    feat["vol_ratio_hour"] = (v / (hour_avg + 1)).fillna(1.0)
    feat["vol_ratio_20"]   = v / (v.rolling(20).mean() + 1)

    # ── Volatility ────────────────────────────────────────────────────────────
    atr = F.atr(h, l, c, 14)
    feat["atr_pct"]        = atr / c * 100
    feat["intraday_range"] = (h - l) / c * 100
    feat["hvol_20h"]       = c.pct_change().rolling(20).std() * np.sqrt(252 * 6) * 100

    # ── F&O expiry ────────────────────────────────────────────────────────────
    fo_days = _fo_expiry_days(idx)
    feat["fo_days_left"]   = fo_days.values
    feat["is_expiry_week"] = (fo_days <= 5).astype(int).values
    feat["is_expiry_day"]  = (fo_days == 0).astype(int).values

    # ── Market context ────────────────────────────────────────────────────────
    nifty_aligned     = nifty.reindex(idx, method="ffill")
    banknifty_aligned = banknifty.reindex(idx, method="ffill")
    vix_aligned       = vix.reindex(idx, method="ffill")

    feat["nifty_ret1h"]     = nifty_aligned.pct_change(1) * 100
    feat["nifty_ret3h"]     = nifty_aligned.pct_change(3) * 100
    feat["banknifty_ret1h"] = banknifty_aligned.pct_change(1) * 100
    feat["vix_level"]       = vix_aligned

    # ── Day of week ───────────────────────────────────────────────────────────
    feat["day_of_week"] = idx.dayofweek   # 0=Mon, 3=Thu (expiry day)
    feat["is_thursday"] = (idx.dayofweek == 3).astype(int)

    return feat.replace([np.inf, -np.inf], np.nan)


def build_labels(df: pd.DataFrame) -> pd.Series:
    fwd = df["Close"].shift(-FORWARD_HOURS) / df["Close"] - 1
    return (fwd * 100 > LABEL_THRESHOLD).astype(int)

# ── Promotion gate ────────────────────────────────────────────────────────────

def _incumbent_auc(X_val: "pd.DataFrame", y_val: "pd.Series") -> float:
    if not MODEL_PATH.exists():
        return 0.0
    try:
        with open(MODEL_PATH, "rb") as f:
            saved = pickle.load(f)
        if "auc" in saved:
            return float(saved["auc"])
        from sklearn.metrics import roc_auc_score
        X_aligned = X_val.reindex(columns=saved["features"], fill_value=0).fillna(0)
        return float(roc_auc_score(y_val, saved["model"].predict_proba(X_aligned)[:, 1]))
    except Exception:
        return 0.0


def _save_if_better(model, features: list, new_auc: float,
                    X_val: "pd.DataFrame", y_val: "pd.Series",
                    brier_uncal: Optional[float] = None,
                    brier_cal: Optional[float] = None,
                    walk_forward: "Optional['WalkForwardPnL']" = None,
                    min_net_pnl_pct: float = 0.0) -> bool:
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
    MODEL_PATH.parent.mkdir(exist_ok=True)
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
    """Train the 1h intraday model with isotonic probability calibration.

    Stage 1A: same calibration treatment as the daily model. Saved model is
    a `CalibratedClassifierCV`, predict_proba is calibrated.
    """
    from sklearn.ensemble import GradientBoostingClassifier
    from sklearn.calibration import CalibratedClassifierCV
    from sklearn.model_selection import TimeSeriesSplit
    from sklearn.metrics import roc_auc_score, classification_report, brier_score_loss

    # Load market context
    def load_series(name):
        p = DATA_DIR / f"{name}.parquet"
        if not p.exists(): return pd.Series(dtype=float)
        df = pd.read_parquet(p)
        df.index = pd.to_datetime(df.index, utc=True).tz_localize(None) if df.index.tz else df.index
        return df["Close"]

    nifty     = load_series("NIFTY_1h")
    banknifty = load_series("BANKNIFTY_1h")
    vix       = load_series("VIX_1h")

    all_X, all_y, all_fwd = [], [], []
    parquets = sorted(DATA_DIR.glob("*.parquet"))
    parquets = [p for p in parquets if not any(x in p.stem for x in ["NIFTY","BANKNIFTY","VIX","model"])]

    print(f"Building dataset from {len(parquets)} stocks...")
    for path in parquets:
        df = pd.read_parquet(path)
        df.index = pd.to_datetime(df.index, utc=True).tz_localize(None) if df.index.tz else df.index
        df = df.dropna(subset=["Open","High","Low","Close","Volume"])
        if len(df) < 200:
            continue
        feat   = build_features(df, nifty, banknifty, vix)
        labels = build_labels(df)
        fwd_pct = (df["Close"].shift(-FORWARD_HOURS) / df["Close"] - 1) * 100
        combined = (feat
                    .join(labels.rename("label"))
                    .join(fwd_pct.rename("fwd_pct"))
                    .dropna()
                    .iloc[:-FORWARD_HOURS])
        all_X.append(combined.drop(["label", "fwd_pct"], axis=1))
        all_y.append(combined["label"])
        all_fwd.append(combined["fwd_pct"])

    X = pd.concat(all_X).reset_index(drop=True)
    y = pd.concat(all_y).reset_index(drop=True)
    fwd_returns = pd.concat(all_fwd).reset_index(drop=True)

    print(f"Total: {len(X):,} samples | {len(X.columns)} features | {y.mean()*100:.1f}% positive")

    base_params = dict(
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

        base_fold = GradientBoostingClassifier(**base_params)
        base_fold.fit(X_tr, y_tr)
        p_unc = base_fold.predict_proba(X_vl)[:, 1]

        # Calibrated: 80/20 within-fold split
        split = max(100, int(len(tr_idx) * 0.8))
        if split < len(tr_idx) - 50:
            fit_idx, cal_idx = tr_idx[:split], tr_idx[split:]
            base_for_cal = GradientBoostingClassifier(**base_params)
            base_for_cal.fit(X.iloc[fit_idx].fillna(0), y.iloc[fit_idx])
            cal = CalibratedClassifierCV(estimator=base_for_cal, method="isotonic", cv="prefit")
            cal.fit(X.iloc[cal_idx].fillna(0), y.iloc[cal_idx])
            p_cal = cal.predict_proba(X_vl)[:, 1]
        else:
            p_cal = p_unc

        auc_scores.append(roc_auc_score(y_vl, p_unc))
        brier_uncal_scores.append(brier_score_loss(y_vl, p_unc))
        brier_cal_scores.append(brier_score_loss(y_vl, p_cal))
        print(f"  Fold {fold+1}: AUC={auc_scores[-1]:.4f} | Brier uncal={brier_uncal_scores[-1]:.4f} cal={brier_cal_scores[-1]:.4f}")

    mean_auc = float(np.mean(auc_scores))
    mean_brier_u = float(np.mean(brier_uncal_scores))
    mean_brier_c = float(np.mean(brier_cal_scores))
    delta = (mean_brier_u - mean_brier_c) / mean_brier_u * 100 if mean_brier_u else 0.0
    print(f"\nMean AUC:         {mean_auc:.4f} ± {np.std(auc_scores):.4f}")
    print(f"Mean Brier uncal: {mean_brier_u:.4f}")
    print(f"Mean Brier cal:   {mean_brier_c:.4f}  ({delta:+.1f}% improvement)")

    # Final calibrated model
    n_total = len(X)
    n_calib = max(200, n_total // 5)
    fit_X = X.iloc[:-n_calib].fillna(0); fit_y = y.iloc[:-n_calib]
    cal_X = X.iloc[-n_calib:].fillna(0); cal_y = y.iloc[-n_calib:]

    base_final = GradientBoostingClassifier(**base_params)
    base_final.fit(fit_X, fit_y)
    final_model = CalibratedClassifierCV(estimator=base_final, method="isotonic", cv="prefit")
    final_model.fit(cal_X, cal_y)

    importance = pd.Series(base_final.feature_importances_, index=X.columns).nlargest(20)
    print("\nTop 20 features:")
    for name, imp in importance.items():
        print(f"  {name:<25} {imp:.4f} {'█' * int(imp * 300)}")

    # Stage 1C: walk-forward net-P&L gate (round-trip costs from core.costs).
    from core.promotion_gate import walk_forward_pnl
    cal_proba = final_model.predict_proba(cal_X)[:, 1]
    cal_fwd   = fwd_returns.iloc[-n_calib:].values
    wf = walk_forward_pnl(cal_proba, cal_fwd, threshold=0.55)
    print(f"\nWalk-forward (calibration slice, last {n_calib} rows):")
    print(f"  trades={wf.n_trades}/{wf.n_eval}  "
          f"net P&L={wf.net_pnl_pct:+.2f}%  "
          f"mean={wf.mean_pnl_pct:+.3f}%  "
          f"win rate={wf.win_rate_pct:.1f}%  "
          f"(cost {wf.cost_per_trade_pct:.3f}%/trade)")

    _save_if_better(final_model, list(X.columns), mean_auc, X_vl.fillna(0), y_vl,
                    brier_uncal=mean_brier_u, brier_cal=mean_brier_c,
                    walk_forward=wf)

    print(f"\nClassification report (last fold, calibrated):")
    print(classification_report(y_vl, (final_model.predict_proba(X_vl)[:, 1] >= 0.5).astype(int)))


# ── Predict ───────────────────────────────────────────────────────────────────

def dynamic_threshold(vix: float, regime: str, hour: int, fo_days: int) -> float:
    """Compute entry threshold adjusted for current market conditions."""
    threshold = 0.55  # base

    # VIX adjustment — fear = raise bar
    if vix > 25:   threshold += 0.08
    elif vix > 20: threshold += 0.04
    elif vix < 13: threshold -= 0.03  # low vol = cleaner moves

    # Regime adjustment
    if regime == "trending_bull":   threshold -= 0.03
    elif regime == "trending_bear": threshold += 0.05
    elif regime == "high_volatility": threshold += 0.05

    # Time of day — opening noise and closing chop
    if hour == 9:    threshold += 0.04   # gap fill risk
    elif hour == 15: threshold += 0.03   # closing volatility

    # F&O expiry — unpredictable pinning/unwinding
    if fo_days == 0:   threshold += 0.07  # expiry day
    elif fo_days <= 2: threshold += 0.03  # expiry week

    return round(min(0.80, max(0.45, threshold)), 2)


def predict(symbol: str) -> dict:
    if not MODEL_PATH.exists():
        raise FileNotFoundError("Model not trained. Run: python3 india_intraday_model.py train")

    with open(MODEL_PATH, "rb") as f:
        saved = pickle.load(f)
    model, features = saved["model"], saved["features"]

    sym = symbol.replace(".NS","").replace("-","_")
    path = DATA_DIR / f"{sym}.parquet"
    if not path.exists():
        raise FileNotFoundError(f"No 1h data for {symbol}. Run fetch first.")

    df = pd.read_parquet(path)
    df.index = pd.to_datetime(df.index, utc=True).tz_localize(None) if df.index.tz else df.index

    def load_series(name):
        p = DATA_DIR / f"{name}.parquet"
        if not p.exists(): return pd.Series(dtype=float)
        d = pd.read_parquet(p)
        d.index = pd.to_datetime(d.index, utc=True).tz_localize(None) if d.index.tz else d.index
        return d["Close"]

    feat = build_features(df, load_series("NIFTY_1h"), load_series("BANKNIFTY_1h"), load_series("VIX_1h"))
    latest = feat.iloc[[-1]][features].fillna(0)
    proba  = model.predict_proba(latest)[0][1]
    signal = "BUY" if proba >= 0.55 else ("HOLD" if proba >= 0.40 else "SKIP")

    # B.2: surface the result under both old and new key names so callers
    # transitioning to the disambiguated `ml_1h_*` form keep working.
    return {
        "symbol": symbol,
        "ml_1h_signal":     signal,
        "ml_1h_proba":      round(float(proba), 4),
        "ml_1h_confidence": round(float(proba) * 100, 1),
        # Legacy aliases — remove after one release cycle.
        "intraday_signal":     signal,
        "intraday_proba":      round(float(proba), 4),
        "intraday_confidence": round(float(proba) * 100, 1),
    }


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "help"

    if cmd == "fetch":
        fetch_all()
    elif cmd == "train":
        train()
    elif cmd == "predict":
        sym = sys.argv[2].upper() if len(sys.argv) > 2 else "TATACONSUM"
        r = predict(sym)
        print(f"\n{sym}: {r['intraday_signal']} (proba={r['intraday_proba']:.4f}, confidence={r['intraday_confidence']}%)")
    else:
        print("Usage: python3 india_intraday_model.py fetch | train | predict SYMBOL")
