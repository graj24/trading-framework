"""core/replay.py — date-range replay harness.

Generalises simulate_day.py to a full date range.  For each trading day in
[start, end] the pipeline runs with only the data that would have been
available at that timestamp (point-in-time slicing of parquet files).

Usage
-----
python -m core.replay --start 2025-01-01 --end 2025-03-31 --symbols RELIANCE TCS
python -m core.replay --start 2025-01-01 --end 2025-03-31  # uses config.yaml watchlist

The replay writes trades to a *separate* SQLite file (replay_trades.db by
default) so it never pollutes the live paper_trades.db.
"""
from __future__ import annotations

import argparse
import logging
import sqlite3
import uuid
from datetime import date, timedelta
from pathlib import Path
from typing import Iterator

import numpy as np
import pandas as pd

from core.costs import SLIPPAGE_FRAC as SLIPPAGE, BROKERAGE_FRAC as BROKERAGE
from core.holidays import is_trading_day
from core.knowledge_base import kb_path

logger = logging.getLogger(__name__)

REPLAY_DB = Path("replay_trades.db")
CAPITAL   = 10_000
POS_PCT   = 0.15


# ── DB helpers ────────────────────────────────────────────────────────────────

def _init_db(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS replay_trades (
            id           TEXT PRIMARY KEY,
            symbol       TEXT,
            date         TEXT,
            entry        REAL,
            exit         REAL,
            qty          INTEGER,
            exit_reason  TEXT,
            pnl_pct      REAL,
            pnl_inr      REAL,
            win          INTEGER,
            signal       TEXT
        )
    """)
    conn.commit()
    conn.close()


def _insert(db_path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    conn = sqlite3.connect(db_path)
    conn.executemany(
        """INSERT OR IGNORE INTO replay_trades
           (id, symbol, date, entry, exit, qty, exit_reason, pnl_pct, pnl_inr, win, signal)
           VALUES (:id,:symbol,:date,:entry,:exit,:qty,:exit_reason,:pnl_pct,:pnl_inr,:win,:signal)""",
        rows,
    )
    conn.commit()
    conn.close()


# ── Trading-day iterator ──────────────────────────────────────────────────────

def _trading_days(start: date, end: date) -> Iterator[date]:
    d = start
    while d <= end:
        if is_trading_day(d):
            yield d
        d += timedelta(days=1)


# ── Point-in-time data slice ──────────────────────────────────────────────────

def _pit_slice(symbol: str, as_of: date) -> pd.DataFrame | None:
    """Return price history for *symbol* up to and including *as_of*."""
    path = kb_path(symbol) / "price_history.parquet"
    if not path.exists():
        return None
    df = pd.read_parquet(path).sort_index()
    df.index = pd.to_datetime(df.index, utc=True).tz_localize(None)
    df = df[df.index.date <= as_of]
    return df if len(df) >= 30 else None


# ── Gap signal (same logic as GapStrategy) ────────────────────────────────────

def _gap_signal(df: pd.DataFrame, gap_threshold: float = 2.0) -> dict | None:
    """Return a trade dict for the last row if it qualifies as a gap-up, else None."""
    if len(df) < 2:
        return None

    row      = df.iloc[-1]
    prev_row = df.iloc[-2]

    gap_pct = (row["Open"] - prev_row["Close"]) / prev_row["Close"] * 100
    if gap_pct < gap_threshold:
        return None

    # Filters
    ema50 = float(df["Close"].ewm(span=50, adjust=False).mean().iloc[-1])
    ema12 = df["Close"].ewm(span=12, adjust=False).mean()
    ema26 = df["Close"].ewm(span=26, adjust=False).mean()
    macd  = ema12 - ema26
    macd_bull = float(macd.iloc[-1]) > float((macd - macd.ewm(span=9, adjust=False).mean()).iloc[-1])
    vol_avg20 = float(df["Volume"].rolling(20).mean().iloc[-1])

    if row["Volume"] < vol_avg20 * 1.5:
        return None
    if prev_row["Close"] < ema50:
        return None

    entry  = round(float(row["Open"]) * (1 + SLIPPAGE), 2)
    sl     = round(float(prev_row["Close"]) * 1.002, 2)
    t2     = round(float(row["Open"]) * (1 + gap_pct * 2 / 100), 2)
    t1     = round(float(row["Open"]) * (1 + gap_pct / 100), 2)
    qty    = max(1, int(CAPITAL * POS_PCT / entry))

    low, high, close = float(row["Low"]), float(row["High"]), float(row["Close"])

    if low <= sl:
        exit_p, reason = sl, "SL"
    elif high >= t2:
        exit_p, reason = t2, "T2"
    elif high >= t1:
        exit_p = max(round(high * 0.995, 2), close)
        reason = "Trail"
    else:
        exit_p, reason = close, "Close"

    brok    = (entry + exit_p) * qty * BROKERAGE
    pnl_inr = (exit_p - entry) * qty - brok
    pnl_pct = (exit_p - entry) / entry * 100

    return {
        "id":          str(uuid.uuid4())[:8],
        "symbol":      "",
        "date":        str(df.index[-1].date()),
        "entry":       entry,
        "exit":        round(exit_p, 2),
        "qty":         qty,
        "exit_reason": reason,
        "pnl_pct":     round(pnl_pct, 3),
        "pnl_inr":     round(pnl_inr, 2),
        "win":         int(pnl_inr > 0),
        "signal":      "gap",
    }


# ── Replay engine ─────────────────────────────────────────────────────────────

def replay(
    symbols: list[str],
    start: date,
    end: date,
    gap_threshold: float = 2.0,
    db_path: Path = REPLAY_DB,
) -> pd.DataFrame:
    """Run the gap strategy day-by-day over [start, end] for each symbol.

    Returns a DataFrame of all simulated trades.
    """
    _init_db(db_path)
    all_rows: list[dict] = []

    days = list(_trading_days(start, end))
    logger.info("Replay: %d symbols × %d trading days", len(symbols), len(days))

    for sym in symbols:
        for d in days:
            df = _pit_slice(sym, d)
            if df is None or df.index[-1].date() != d:
                # No data for this day (holiday, data gap, or future)
                continue
            trade = _gap_signal(df, gap_threshold)
            if trade:
                trade["symbol"] = sym
                all_rows.append(trade)

    _insert(db_path, all_rows)
    logger.info("Replay complete: %d trades written to %s", len(all_rows), db_path)
    return pd.DataFrame(all_rows)


def print_replay_report(df: pd.DataFrame, start: date, end: date) -> None:
    if df.empty:
        print("No trades generated in replay.")
        return

    wins   = df[df["win"] == 1]
    losses = df[df["win"] == 0]
    pf     = (wins["pnl_inr"].sum() / abs(losses["pnl_inr"].sum())
              if len(losses) and losses["pnl_inr"].sum() != 0 else float("inf"))

    SEP = "=" * 65
    print(f"\n{SEP}")
    print(f"  REPLAY REPORT  {start} → {end}")
    print(SEP)
    print(f"  Symbols        : {df['symbol'].nunique()}")
    print(f"  Total Trades   : {len(df)}")
    print(f"  Win Rate       : {df['win'].mean()*100:.1f}%  ({len(wins)}W / {len(losses)}L)")
    print(f"  Total Net P&L  : ₹{df['pnl_inr'].sum():+,.2f}")
    print(f"  Profit Factor  : {pf:.2f}x")
    print(f"  Worst Trade    : {df['pnl_pct'].min():.2f}%")
    print(SEP)

    print(f"\n  {'SYMBOL':<14} {'TRADES':>7} {'WIN%':>6} {'NET P&L':>10}")
    print(f"  {'-'*42}")
    for sym, grp in df.groupby("symbol"):
        wr  = grp["win"].mean() * 100
        pnl = grp["pnl_inr"].sum()
        print(f"  {sym:<14} {len(grp):>7} {wr:>5.1f}% {pnl:>+10.2f}")
    print(f"\n{SEP}\n")


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import yaml
    from core.logger import setup_logging

    logging.basicConfig(level=logging.INFO)

    parser = argparse.ArgumentParser(description="Date-range replay harness")
    parser.add_argument("--start",     required=True, help="Start date YYYY-MM-DD")
    parser.add_argument("--end",       required=True, help="End date YYYY-MM-DD")
    parser.add_argument("--symbols",   nargs="*",     default=None)
    parser.add_argument("--threshold", type=float,    default=2.0)
    parser.add_argument("--db",        default=str(REPLAY_DB))
    args = parser.parse_args()

    if args.symbols:
        symbols = args.symbols
    else:
        with open("config.yaml") as f:
            symbols = yaml.safe_load(f).get("watchlist", [])

    if not symbols:
        print("No symbols. Pass --symbols or set watchlist in config.yaml.")
        raise SystemExit(1)

    start = date.fromisoformat(args.start)
    end   = date.fromisoformat(args.end)
    db    = Path(args.db)

    df = replay(symbols, start, end, gap_threshold=args.threshold, db_path=db)
    print_replay_report(df, start, end)
