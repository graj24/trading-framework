"""
Gap Strategy Backtest — realistic win rate across ALL gap days (>= 2%).

For every trading day where open gaps up >= 2% vs prev close:
  - Entry = open + 0.1% slippage
  - SL    = prev close + 0.2% (gap-fill stop)
  - T1    = open + 1× gap%
  - T2    = open + 2× gap%
  - Trail = 0.5% below peak, activates after +1%
  - Exit  = T2 hit | trailing SL | market close

Uses only OHLC daily data (no intraday ticks) — outcome approximated from High/Low/Close.
"""
from __future__ import annotations

import sys
import yaml
import numpy as np
import pandas as pd
from pathlib import Path
from core.knowledge_base import kb_path

with open("config.yaml") as f:
    config = yaml.safe_load(f)

CAPITAL      = config["trading"]["capital"]
POSITION_PCT = 0.15          # 15% of capital per trade
SLIPPAGE     = 0.001
GAP_THRESHOLD = float(sys.argv[1]) if len(sys.argv) > 1 else 2.0  # %

symbols = config["watchlist"]

all_trades = []

for symbol in symbols:
    path = kb_path(symbol) / "price_history.parquet"
    if not path.exists():
        continue

    df = pd.read_parquet(path).sort_index()
    df.index = pd.to_datetime(df.index, utc=True).tz_localize(None)
    df = df.dropna(subset=["Open", "High", "Low", "Close"])
    df["prev_close"] = df["Close"].shift(1)
    df["gap_pct"] = (df["Open"] - df["prev_close"]) / df["prev_close"] * 100

    # Pre-compute indicators for filtering
    df["ema50"]       = df["Close"].ewm(span=50, adjust=False).mean()
    ema12             = df["Close"].ewm(span=12, adjust=False).mean()
    ema26             = df["Close"].ewm(span=26, adjust=False).mean()
    macd              = ema12 - ema26
    df["macd_bull"]   = (macd - macd.ewm(span=9, adjust=False).mean()) > 0
    df["vol_avg20"]   = df["Volume"].rolling(20).mean()

    gap_days = df[df["gap_pct"] >= GAP_THRESHOLD].copy()

    for date, row in gap_days.iterrows():
        prev_close = row["prev_close"]
        open_p     = row["Open"]
        high       = row["High"]
        low        = row["Low"]
        close      = row["Close"]
        gap        = row["gap_pct"]

        # FILTER: volume > 1.5× avg, price above EMA50, MACD bullish
        if row["Volume"] < row["vol_avg20"] * 1.5:
            continue
        if prev_close < row["ema50"]:
            continue
        if not row["macd_bull"]:
            continue

        entry  = round(open_p * (1 + SLIPPAGE), 2)
        # Gap-fill SL: prev close is the thesis invalidation level
        sl     = round(prev_close * 1.002, 2)
        t1     = round(open_p * (1 + gap / 100), 2)
        t2     = round(open_p * (1 + gap * 2 / 100), 2)
        pos    = CAPITAL * POSITION_PCT
        qty    = max(1, int(pos / entry))

        # Approximate intraday outcome using daily OHLC:
        # Assume worst case (low) happens before best case (high) — conservative
        exit_price  = None
        exit_reason = None

        if low <= sl:
            # Gap filled — stopped out
            exit_price  = sl
            exit_reason = "SL"
        elif high >= t2:
            exit_price  = t2
            exit_reason = "T2"
        elif high >= t1:
            # Hit T1, trail from there — approximate: trail 0.5% below close
            trail_sl = round(high * 0.995, 2)
            exit_price  = max(trail_sl, close)
            exit_reason = "Trail"
        else:
            exit_price  = close
            exit_reason = "Close"

        brokerage = (entry + exit_price) * qty * 0.0003
        pnl_pct   = (exit_price - entry) / entry * 100
        pnl_inr   = (exit_price - entry) * qty - brokerage

        all_trades.append({
            "symbol":      symbol,
            "date":        date.strftime("%Y-%m-%d"),
            "gap_pct":     round(gap, 2),
            "entry":       entry,
            "exit":        exit_price,
            "exit_reason": exit_reason,
            "pnl_pct":     round(pnl_pct, 2),
            "pnl_inr":     round(pnl_inr, 2),
            "win":         pnl_inr > 0,
        })

# ── Results ───────────────────────────────────────────────────────────────────
trades_df = pd.DataFrame(all_trades)
if trades_df.empty:
    print("No gap days found.")
    exit()

wins   = trades_df[trades_df["win"]]
losses = trades_df[~trades_df["win"]]

total_trades = len(trades_df)
win_rate     = len(wins) / total_trades * 100
total_pnl    = trades_df["pnl_inr"].sum()
avg_win      = wins["pnl_inr"].mean() if len(wins) else 0
avg_loss     = losses["pnl_inr"].mean() if len(losses) else 0
profit_factor = wins["pnl_inr"].sum() / abs(losses["pnl_inr"].sum()) if len(losses) else float("inf")
max_dd_pct   = trades_df["pnl_pct"].min()

SEP = "=" * 65
print(f"\n{SEP}")
print(f"  GAP STRATEGY BACKTEST  (gap >= {GAP_THRESHOLD}%)")
print(f"  Stocks: {', '.join(symbols)}")
print(f"  Period: {trades_df['date'].min()} → {trades_df['date'].max()}")
print(SEP)
print(f"  Total Trades     : {total_trades}")
print(f"  Win Rate         : {win_rate:.1f}%  ({len(wins)}W / {len(losses)}L)")
print(f"  Total Net P&L    : ₹{total_pnl:+,.2f}")
print(f"  Avg Win          : ₹{avg_win:+.2f}")
print(f"  Avg Loss         : ₹{avg_loss:+.2f}")
print(f"  Profit Factor    : {profit_factor:.2f}x")
print(f"  Worst Single Day : {max_dd_pct:.2f}%")
print(SEP)

# Per-symbol breakdown
print(f"\n  {'SYMBOL':<12} {'TRADES':>7} {'WIN%':>6} {'NET P&L':>10} {'AVG/TRADE':>10}")
print(f"  {'-'*50}")
for sym, grp in trades_df.groupby("symbol"):
    wr  = grp["win"].mean() * 100
    pnl = grp["pnl_inr"].sum()
    avg = grp["pnl_inr"].mean()
    print(f"  {sym:<12} {len(grp):>7} {wr:>5.1f}% {pnl:>+10,.2f} {avg:>+10.2f}")

# Exit reason breakdown
print(f"\n  Exit Reason Breakdown:")
for reason, grp in trades_df.groupby("exit_reason"):
    wr = grp["win"].mean() * 100
    print(f"    {reason:<8}: {len(grp):>4} trades | win rate {wr:.1f}% | avg ₹{grp['pnl_inr'].mean():+.2f}")

print(f"\n{SEP}\n")
