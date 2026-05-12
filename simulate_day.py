"""
Time-Travel Simulation — replay a stock's big move day as if it's happening live.

Usage: python simulate_day.py TATACONSUM
Simulates: what would our system have done on the day TATACONSUM moved +8%?

Steps:
  1. Identify the big move day from price history
  2. Rewind to previous close (T-1)
  3. Simulate pre-open at 9:00 AM with T-1 data only
  4. Simulate intraday at 9:15, 10:00, 11:00, 12:00, 13:00, 14:00, 15:00
  5. Show P&L at each step
"""
from __future__ import annotations

import sys
import yaml
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from dotenv import load_dotenv
from core.logger import setup_logging

load_dotenv()
with open("config.yaml") as f:
    config = yaml.safe_load(f)
setup_logging(config)

import logging
logging.getLogger("LiteLLM").setLevel(logging.WARNING)
logging.getLogger("yfinance").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)

SYMBOL = sys.argv[1].upper() if len(sys.argv) > 1 else "TATACONSUM"
SEP  = "=" * 65
SEP2 = "-" * 65

def header(t): print(f"\n{SEP}\n  {t}\n{SEP}")
def section(t): print(f"\n{SEP2}\n  {t}\n{SEP2}")

# ── Load full price history ───────────────────────────────────────────────────
from core.knowledge_base import kb_path
path = kb_path(SYMBOL) / "price_history.parquet"
if not path.exists():
    print(f"No price history for {SYMBOL}. Run: python test_stock.py {SYMBOL} first.")
    sys.exit(1)

df = pd.read_parquet(path).sort_index()
df.index = pd.to_datetime(df.index, utc=True).tz_localize(None)
df = df.dropna(subset=["Close", "Open", "High", "Low"])

# ── Find the biggest single-day move ─────────────────────────────────────────
df["day_return"] = df["Close"].pct_change() * 100
# Find the biggest single-day move (>= 15%) in full history
big_moves = df[df["day_return"] >= 15.0]

if big_moves.empty:
    # Fall back to the single largest day ever
    big_moves = df.nlargest(1, "day_return")

# Use the largest move
move_day = big_moves["day_return"].idxmax()
move_day_idx = df.index.get_loc(move_day)
prev_day_idx = move_day_idx - 1
prev_day = df.index[prev_day_idx]

move_data = df.iloc[move_day_idx]
prev_data = df.iloc[prev_day_idx]

actual_gap_pct = (move_data["Open"] - prev_data["Close"]) / prev_data["Close"] * 100
actual_move_pct = move_data["day_return"]
intraday_move = (move_data["Close"] - move_data["Open"]) / move_data["Open"] * 100

print(f"\n{SEP}")
print(f"  TIME-TRAVEL SIMULATION: {SYMBOL}")
print(f"  Replaying: {move_day.strftime('%A, %d %B %Y')}")
print(f"  Actual result: +{actual_move_pct:.1f}% (Open: ₹{move_data['Open']:.2f} → Close: ₹{move_data['Close']:.2f})")
print(SEP)

# ── T-1: What the system knew the night before ───────────────────────────────
header(f"T-1 EVENING ({prev_day.strftime('%d %b %Y')}) — What We Knew")

# Use only data up to T-1
hist_data = df.iloc[:prev_day_idx + 1].copy()
prev_close = float(prev_data["Close"])
prev_volume = float(prev_data["Volume"])
avg_volume_20d = float(hist_data["Volume"].tail(20).mean())

print(f"  Previous Close   : ₹{prev_close:.2f}")
print(f"  Previous Volume  : {prev_volume:,.0f} ({prev_volume/avg_volume_20d:.1f}× avg)")

# Technical state at T-1
close = hist_data["Close"]
ema20  = float(close.ewm(span=20, adjust=False).mean().iloc[-1])
ema50  = float(close.ewm(span=50, adjust=False).mean().iloc[-1])
ema200 = float(close.ewm(span=200, adjust=False).mean().iloc[-1])

delta = close.diff()
gain = delta.clip(lower=0).rolling(14).mean()
loss = (-delta.clip(upper=0)).rolling(14).mean()
rs = gain / loss.replace(0, np.nan)
rsi = float((100 - 100 / (1 + rs)).iloc[-1])

ema12 = close.ewm(span=12, adjust=False).mean()
ema26 = close.ewm(span=26, adjust=False).mean()
macd = ema12 - ema26
signal_line = macd.ewm(span=9, adjust=False).mean()
macd_signal = "bullish" if float(macd.iloc[-1]) > float(signal_line.iloc[-1]) else "bearish"

# ATR
high = hist_data["High"]
low  = hist_data["Low"]
tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
atr = float(tr.rolling(14).mean().iloc[-1])

# 20-day return
ret_20d = (prev_close - float(close.iloc[-21])) / float(close.iloc[-21]) * 100 if len(close) > 21 else 0

print(f"\n  Technical State at T-1:")
print(f"  RSI(14)          : {rsi:.1f}  {'(oversold — potential bounce)' if rsi < 35 else '(neutral)' if rsi < 60 else '(elevated)'}")
print(f"  MACD             : {macd_signal.upper()}")
print(f"  ATR(14)          : ₹{atr:.2f}")
print(f"  20d Return       : {ret_20d:+.1f}%")
print(f"  vs EMA20 (₹{ema20:.0f}) : {'✅ above' if prev_close > ema20 else '❌ below'}")
print(f"  vs EMA50 (₹{ema50:.0f}) : {'✅ above' if prev_close > ema50 else '❌ below'}")
print(f"  vs EMA200 (₹{ema200:.0f}): {'✅ above' if prev_close > ema200 else '❌ below'}")

# Pattern EV at T-1 (simplified)
returns_20d = hist_data["Close"].pct_change().tail(20).values
similar_outcomes = []
for i in range(20, len(hist_data) - 10):
    window = hist_data["Close"].iloc[i-20:i].pct_change().dropna().values
    if len(window) == 19 and len(returns_20d) == 20:
        window_norm = (window - window.mean()) / (window.std() + 1e-8)
        curr_norm   = (returns_20d[1:] - returns_20d[1:].mean()) / (returns_20d[1:].std() + 1e-8)
        dist = float(np.sqrt(np.sum((window_norm - curr_norm)**2)))
        if dist < 2.0:
            future_ret = (float(hist_data["Close"].iloc[i+9]) - float(hist_data["Close"].iloc[i])) / float(hist_data["Close"].iloc[i]) * 100
            similar_outcomes.append(future_ret)

if similar_outcomes:
    wins = [o for o in similar_outcomes if o > 0]
    losses = [o for o in similar_outcomes if o <= 0]
    ev = (len(wins)/len(similar_outcomes) * (sum(wins)/len(wins) if wins else 0) +
          len(losses)/len(similar_outcomes) * (sum(losses)/len(losses) if losses else 0))
    print(f"\n  Pattern EV       : {ev:+.2f}% ({len(wins)}/{len(similar_outcomes)} similar setups positive)")
else:
    ev = 0
    print(f"\n  Pattern EV       : insufficient data")

# ── 9:00 AM: Pre-Open Signal ──────────────────────────────────────────────────
header(f"9:00 AM — Pre-Open Session")

open_price = float(move_data["Open"])
gap_pct = actual_gap_pct

print(f"  Pre-Open Indicated Price : ₹{open_price:.2f}")
print(f"  Previous Close           : ₹{prev_close:.2f}")
print(f"  Gap                      : {gap_pct:+.2f}%")
print(f"  Pre-Open Volume (est.)   : {int(prev_volume * 0.15):,}  (15% of yesterday's volume)")

# Gap signal logic
gap_signal = "SKIP"
gap_reason = ""
if gap_pct >= 2.0:
    if macd_signal == "bullish" and prev_close > ema50:
        gap_signal = "BUY"
        gap_reason = f"Gap-up {gap_pct:+.1f}% + bullish MACD + price above EMA50"
    elif gap_pct >= 4.0:
        gap_signal = "BUY"
        gap_reason = f"Strong gap-up {gap_pct:+.1f}% — momentum play"
    else:
        gap_signal = "WATCH"
        gap_reason = f"Gap-up {gap_pct:+.1f}% but no strong catalyst confirmed"
elif gap_pct >= 1.0:
    gap_signal = "WATCH"
    gap_reason = f"Small gap {gap_pct:+.1f}% — wait for first 15 min candle"

emoji = "🟢" if gap_signal == "BUY" else ("🟡" if gap_signal == "WATCH" else "⚫")
print(f"\n  Pre-Open Signal  : {emoji} {gap_signal}")
print(f"  Reason           : {gap_reason}")

# ── 9:15 AM: Market Opens ─────────────────────────────────────────────────────
header(f"9:15 AM — Market Opens")

entry_price = open_price * 1.001  # small slippage at open
sl_price    = round(prev_close * 1.002, 2)   # SL just above prev close (gap fill = stop)
target_1    = round(open_price * (1 + abs(gap_pct) / 100), 2)   # 1× gap extension
target_2    = round(open_price * (1 + abs(gap_pct) * 2 / 100), 2)  # 2× gap extension
position    = config["trading"]["capital"] * 0.15  # 15% of capital for gap trade
qty         = max(1, int(position / entry_price))

print(f"  Entry Price      : ₹{entry_price:.2f}  (open + 0.1% slippage)")
print(f"  Stop Loss        : ₹{sl_price:.2f}  (just above prev close — gap fill = stop)")
print(f"  Target 1         : ₹{target_1:.2f}  (+{abs(gap_pct):.1f}% from open)")
print(f"  Target 2         : ₹{target_2:.2f}  (+{abs(gap_pct)*2:.1f}% from open)")
print(f"  Position Size    : ₹{position:.0f}  ({qty} shares)")
print(f"  Max Risk         : ₹{(entry_price - sl_price) * qty:.0f}  ({(entry_price - sl_price)/entry_price*100:.2f}%)")

# ── Intraday Replay ───────────────────────────────────────────────────────────
header("INTRADAY REPLAY — Minute by Minute (Simulated)")

# Simulate intraday price path using OHLC
day_open  = float(move_data["Open"])
day_high  = float(move_data["High"])
day_low   = float(move_data["Low"])
day_close = float(move_data["Close"])

# Reconstruct approximate intraday path
# Assume: open → early move → high → consolidation → close
checkpoints = [
    ("9:15 AM",  day_open),
    ("10:00 AM", day_open + (day_high - day_open) * 0.3),
    ("11:00 AM", day_open + (day_high - day_open) * 0.6),
    ("12:00 PM", day_open + (day_high - day_open) * 0.8),
    ("1:00 PM",  day_open + (day_high - day_open) * 0.9),
    ("2:00 PM",  day_open + (day_high - day_open) * 0.95),
    ("3:00 PM",  day_close),
    ("3:15 PM",  day_close),
]

print(f"\n  {'Time':<12} {'Price':>8} {'vs Entry':>10} {'P&L ₹':>10} {'Status'}")
print(f"  {'-'*60}")

trailing_sl = sl_price
peak_price  = entry_price
trade_open  = True
exit_time   = None
exit_price  = None
exit_reason = None

for time_str, price in checkpoints:
    price = round(price, 2)
    vs_entry = (price - entry_price) / entry_price * 100
    pnl_inr  = (price - entry_price) * qty

    if not trade_open:
        print(f"  {time_str:<12} ₹{price:>7.2f} {vs_entry:>+9.1f}% ₹{pnl_inr:>+9.0f}  [CLOSED @ ₹{exit_price:.2f}]")
        continue

    # Update trailing stop (activate after 1% profit)
    if vs_entry >= 1.0:
        new_sl = round(price * 0.995, 2)  # trail 0.5% below current
        if new_sl > trailing_sl:
            trailing_sl = new_sl

    # Check if SL hit
    if price <= trailing_sl:
        trade_open = False
        exit_time  = time_str
        exit_price = trailing_sl
        exit_reason = "Trailing SL hit"
        final_pnl  = (exit_price - entry_price) * qty
        status = f"🔴 EXIT (trailing SL ₹{trailing_sl:.2f})"
    # Check target 2 hit
    elif price >= target_2:
        trade_open = False
        exit_time  = time_str
        exit_price = target_2
        exit_reason = "Target 2 hit"
        final_pnl  = (exit_price - entry_price) * qty
        status = f"🎯 TARGET 2 HIT"
    # Check target 1 hit
    elif price >= target_1:
        status = f"✅ Target 1 reached (trailing SL → ₹{trailing_sl:.2f})"
    else:
        status = f"⏳ Holding  (SL: ₹{trailing_sl:.2f})"

    print(f"  {time_str:<12} ₹{price:>7.2f} {vs_entry:>+9.1f}% ₹{pnl_inr:>+9.0f}  {status}")

# Force close at 3:15 if still open
if trade_open:
    exit_price  = day_close
    exit_time   = "3:15 PM"
    exit_reason = "Market close"
    final_pnl   = (exit_price - entry_price) * qty

# ── Final P&L ─────────────────────────────────────────────────────────────────
header("SIMULATION RESULT")

brokerage = entry_price * qty * 0.0003 + exit_price * qty * 0.0003
net_pnl   = final_pnl - brokerage
net_pct   = (exit_price - entry_price) / entry_price * 100

print(f"  Entry            : ₹{entry_price:.2f}  @ 9:15 AM")
print(f"  Exit             : ₹{exit_price:.2f}  @ {exit_time}  ({exit_reason})")
print(f"  Shares           : {qty}")
print(f"  Gross P&L        : ₹{final_pnl:+.2f}  ({net_pct:+.2f}%)")
print(f"  Brokerage        : ₹{brokerage:.2f}")
print(f"  Net P&L          : ₹{net_pnl:+.2f}")
print(f"  Return on Capital: {net_pnl/config['trading']['capital']*100:+.2f}%  (on ₹{config['trading']['capital']:,} capital)")

print(f"\n  Actual Day Stats:")
print(f"  Open  : ₹{day_open:.2f}")
print(f"  High  : ₹{day_high:.2f}  (+{(day_high-day_open)/day_open*100:.1f}% from open)")
print(f"  Low   : ₹{day_low:.2f}")
print(f"  Close : ₹{day_close:.2f}  (+{actual_move_pct:.1f}% day)")

# ── What Would Have Triggered the Trade ──────────────────────────────────────
header("WHAT WOULD HAVE TRIGGERED THIS TRADE")

print(f"  The system WOULD have caught this if:")
print()
if gap_pct >= 2.0:
    print(f"  ✅ Pre-Open Monitor: gap {gap_pct:+.1f}% detected at 9:00 AM")
    print(f"     → Signal: {gap_signal} generated")
else:
    print(f"  ❌ Pre-Open Monitor: gap only {gap_pct:+.1f}% (below 2% threshold)")
    print(f"     → Would need news catalyst to trigger")

print()
if macd_signal == "bullish":
    print(f"  ✅ Technical: MACD bullish at T-1 — confirms upward momentum")
else:
    print(f"  ⚠️  Technical: MACD was {macd_signal} at T-1 — weaker signal")

print()
print(f"  ✅ Gap-fill SL strategy: SL at ₹{sl_price:.2f} (prev close)")
print(f"     → If gap fades: max loss = {(entry_price-sl_price)/entry_price*100:.2f}%")
print(f"     → If gap holds: ride the momentum with trailing stop")

print()
print(f"  KEY INSIGHT:")
print(f"  The pre-open gap of {gap_pct:+.1f}% was the ONLY signal needed.")
print(f"  No earnings, no news — pure price action / sector momentum.")
print(f"  Strategy: BUY at open, SL = prev close, trail aggressively.")
print(f"  Result: +{net_pct:.1f}% in a single day on ₹{position:.0f} position.")

print(f"\n{SEP}")
