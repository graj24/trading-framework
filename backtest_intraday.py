"""
Intraday Model Backtest — trailing stop + target exit, no fixed hold time.

Entry : when model probability >= THRESHOLD on a 1h candle
Exit  : target hit | trailing stop hit | market close (15:30)

Usage:
  python3 backtest_intraday.py              # all Nifty50 stocks
  python3 backtest_intraday.py TATACONSUM   # single stock
"""
from __future__ import annotations

import sys
import pickle
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

from india_intraday_model import build_features, DATA_DIR, MODEL_PATH

THRESHOLD    = 0.55   # min probability to enter
CAPITAL      = 10_000
POSITION_PCT = 0.15
SLIPPAGE     = 0.001
BROKERAGE    = 0.0003
STOP_PCT     = 1.0    # initial SL below entry (%)
TARGET_PCT   = 2.5    # target above entry (%)
TRAIL_PCT    = 0.5    # trailing stop distance once in profit (%)

# ── Load model ────────────────────────────────────────────────────────────────
if not MODEL_PATH.exists():
    print("Model not found. Run: python3 india_intraday_model.py train")
    sys.exit(1)

with open(MODEL_PATH, "rb") as f:
    saved = pickle.load(f)
model, features = saved["model"], saved["features"]

def load_series(name):
    p = DATA_DIR / f"{name}.parquet"
    if not p.exists(): return pd.Series(dtype=float)
    df = pd.read_parquet(p)
    df.index = pd.to_datetime(df.index, utc=True).tz_localize(None) if df.index.tz else df.index
    return df["Close"]

nifty     = load_series("NIFTY_1h")
banknifty = load_series("BANKNIFTY_1h")
vix       = load_series("VIX_1h")

target_sym = sys.argv[1].upper() if len(sys.argv) > 1 else None
if target_sym:
    parquets = [DATA_DIR / f"{target_sym}.parquet"]
else:
    parquets = sorted(p for p in DATA_DIR.glob("*.parquet")
                      if not any(x in p.stem for x in ["NIFTY","BANKNIFTY","VIX","model"]))

all_trades = []

for path in parquets:
    symbol = path.stem
    df = pd.read_parquet(path)
    df.index = pd.to_datetime(df.index, utc=True).tz_localize(None) if df.index.tz else df.index
    df = df.dropna(subset=["Open","High","Low","Close","Volume"])
    if len(df) < 200:
        continue

    feat  = build_features(df, nifty, banknifty, vix)
    X     = feat[features].fillna(0)
    proba = model.predict_proba(X)[:, 1]
    close = df["Close"].values
    dates = df.index

    i = 0
    while i < len(df) - 1:
        if proba[i] < THRESHOLD:
            i += 1
            continue

        entry  = close[i] * (1 + SLIPPAGE)
        sl     = entry * (1 - STOP_PCT / 100)
        target = entry * (1 + TARGET_PCT / 100)
        trail  = sl
        qty    = max(1, int(CAPITAL * POSITION_PCT / entry))

        exit_price  = None
        exit_reason = None
        entry_date  = dates[i]

        # Walk forward candle by candle until exit
        j = i + 1
        while j < len(df):
            price = close[j]
            same_day = dates[j].date() == entry_date.date()

            # Update trailing stop once in profit
            if price > entry * (1 + TRAIL_PCT / 100):
                new_trail = price * (1 - TRAIL_PCT / 100)
                if new_trail > trail:
                    trail = new_trail

            if price <= trail:
                exit_price, exit_reason = trail, "Trail/SL"
                break
            if price >= target:
                exit_price, exit_reason = target, "Target"
                break
            # Force close at end of day (15:xx candle)
            if not same_day or (j == len(df) - 1):
                exit_price, exit_reason = price, "EOD"
                break
            j += 1

        if exit_price is None:
            i += 1
            continue

        brok    = (entry + exit_price) * qty * BROKERAGE
        pnl_pct = (exit_price - entry) / entry * 100
        pnl_inr = (exit_price - entry) * qty - brok
        hold_h  = j - i

        all_trades.append({
            "symbol":      symbol,
            "datetime":    entry_date,
            "hour":        entry_date.hour,
            "proba":       round(proba[i], 4),
            "entry":       round(entry, 2),
            "exit":        round(exit_price, 2),
            "exit_reason": exit_reason,
            "hold_candles": hold_h,
            "pnl_pct":     round(pnl_pct, 3),
            "pnl_inr":     round(pnl_inr, 2),
            "win":         pnl_inr > 0,
        })

        i = j + 1  # skip to after exit, no overlapping trades

# ── Results ───────────────────────────────────────────────────────────────────
if not all_trades:
    print("No trades generated.")
    sys.exit(0)

trades = pd.DataFrame(all_trades)
wins   = trades[trades["win"]]
losses = trades[~trades["win"]]
pf     = wins["pnl_inr"].sum() / abs(losses["pnl_inr"].sum()) if len(losses) else float("inf")

SEP = "=" * 65
print(f"\n{SEP}")
print(f"  INDIA INTRADAY BACKTEST  (threshold={THRESHOLD}, SL={STOP_PCT}%, T={TARGET_PCT}%)")
print(f"  Period: {trades['datetime'].min().date()} → {trades['datetime'].max().date()}")
print(SEP)
print(f"  Total Trades     : {len(trades)}")
print(f"  Win Rate         : {trades['win'].mean()*100:.1f}%  ({len(wins)}W / {len(losses)}L)")
print(f"  Total Net P&L    : ₹{trades['pnl_inr'].sum():+,.2f}")
print(f"  Avg Win          : ₹{wins['pnl_inr'].mean():+.2f}")
print(f"  Avg Loss         : ₹{losses['pnl_inr'].mean():+.2f}")
print(f"  Profit Factor    : {pf:.2f}x")
print(f"  Avg Hold         : {trades['hold_candles'].mean():.1f} candles ({trades['hold_candles'].mean():.0f}h)")
print(f"  Best Trade       : {trades['pnl_pct'].max():.2f}%")
print(f"  Worst Trade      : {trades['pnl_pct'].min():.2f}%")
print(SEP)

# Per-symbol
if not target_sym:
    print(f"\n  {'SYMBOL':<14} {'TRADES':>7} {'WIN%':>6} {'NET P&L':>10} {'AVG':>8}")
    print(f"  {'-'*50}")
    for sym, grp in trades.groupby("symbol"):
        wr  = grp["win"].mean() * 100
        pnl = grp["pnl_inr"].sum()
        avg = grp["pnl_inr"].mean()
        print(f"  {sym:<14} {len(grp):>7} {wr:>5.1f}% {pnl:>+10.2f} {avg:>+8.2f}")

# Exit reason breakdown
print(f"\n  Exit Reason Breakdown:")
for reason, grp in trades.groupby("exit_reason"):
    wr = grp["win"].mean() * 100
    print(f"    {reason:<10}: {len(grp):>4} trades | win {wr:.1f}% | avg ₹{grp['pnl_inr'].mean():+.2f}")

# By hour of entry
print(f"\n  By Entry Hour:")
print(f"  {'HOUR':<8} {'TRADES':>7} {'WIN%':>6} {'AVG P&L':>9}")
print(f"  {'-'*35}")
for hour, grp in trades.groupby("hour"):
    wr  = grp["win"].mean() * 100
    avg = grp["pnl_inr"].mean()
    print(f"  {hour:02d}:00    {len(grp):>7} {wr:>5.1f}% {avg:>+9.2f}")

print(f"\n{SEP}\n")
