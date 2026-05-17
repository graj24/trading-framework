"""core/backtester.py — unified event-driven backtester.

Strategies
----------
GapStrategy        : daily OHLC gap-up trades (replaces backtest_gap.py)
IntradayMLStrategy : 1h ML-signal trades     (replaces backtest_intraday.py)

CLI
---
python -m core.backtester --strategy gap [--threshold 2.0] [--symbols RELIANCE TCS]
python -m core.backtester --strategy ml_intraday [--threshold 0.55] [--symbols ...]
"""
from __future__ import annotations

import argparse
import pickle
import warnings
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

from core.costs import SLIPPAGE_FRAC as SLIPPAGE, BROKERAGE_FRAC as BROKERAGE
from core.knowledge_base import kb_path

CAPITAL      = 10_000
POSITION_PCT = 0.15


# ── Trade record ──────────────────────────────────────────────────────────────

@dataclass
class Trade:
    symbol:      str
    entry_dt:    pd.Timestamp
    exit_dt:     pd.Timestamp
    entry:       float
    exit:        float
    qty:         int
    exit_reason: str
    strategy:    str

    @property
    def pnl_inr(self) -> float:
        brok = (self.entry + self.exit) * self.qty * BROKERAGE
        return (self.exit - self.entry) * self.qty - brok

    @property
    def pnl_pct(self) -> float:
        return (self.exit - self.entry) / self.entry * 100

    @property
    def win(self) -> bool:
        return self.pnl_inr > 0


# ── Strategy ABC ──────────────────────────────────────────────────────────────

class Strategy(ABC):
    name: str = "base"

    @abstractmethod
    def trades(self, symbol: str) -> Iterator[Trade]:
        """Yield Trade objects for the given symbol."""

    def signal(self, pit_df: pd.DataFrame, symbol: str) -> "Trade | None":
        """Return a Trade for the last row of *pit_df* if it qualifies, else None.

        Override this to support point-in-time replay via replay_strategy().
        The default raises NotImplementedError.
        """
        raise NotImplementedError(f"{self.__class__.__name__} does not implement signal()")


# ── Gap Strategy ──────────────────────────────────────────────────────────────

class GapStrategy(Strategy):
    """Daily gap-up strategy using OHLC data only."""

    name = "gap"

    def __init__(self, threshold: float = 2.0):
        self.threshold = threshold

    def trades(self, symbol: str) -> Iterator[Trade]:
        path = kb_path(symbol) / "price_history.parquet"
        if not path.exists():
            return

        df = pd.read_parquet(path).sort_index()
        df.index = pd.to_datetime(df.index, utc=True).tz_localize(None)
        df = df.dropna(subset=["Open", "High", "Low", "Close", "Volume"])

        df["prev_close"] = df["Close"].shift(1)
        df["gap_pct"]    = (df["Open"] - df["prev_close"]) / df["prev_close"] * 100
        df["ema50"]      = df["Close"].ewm(span=50, adjust=False).mean()
        ema12            = df["Close"].ewm(span=12, adjust=False).mean()
        ema26            = df["Close"].ewm(span=26, adjust=False).mean()
        macd             = ema12 - ema26
        df["macd_bull"]  = (macd - macd.ewm(span=9, adjust=False).mean()) > 0
        df["vol_avg20"]  = df["Volume"].rolling(20).mean()

        for date, row in df[df["gap_pct"] >= self.threshold].iterrows():
            if row["Volume"] < row["vol_avg20"] * 1.5:
                continue
            if row["prev_close"] < row["ema50"]:
                continue
            if not row["macd_bull"]:
                continue

            entry  = round(row["Open"] * (1 + SLIPPAGE), 2)
            sl     = round(row["prev_close"] * 1.002, 2)
            gap    = row["gap_pct"]
            t2     = round(row["Open"] * (1 + gap * 2 / 100), 2)
            t1     = round(row["Open"] * (1 + gap / 100), 2)
            qty    = max(1, int(CAPITAL * POSITION_PCT / entry))

            low, high, close = row["Low"], row["High"], row["Close"]

            if low <= sl:
                exit_p, reason = sl, "SL"
            elif high >= t2:
                exit_p, reason = t2, "T2"
            elif high >= t1:
                trail  = round(high * 0.995, 2)
                exit_p = max(trail, close)
                reason = "Trail"
            else:
                exit_p, reason = close, "Close"

            yield Trade(
                symbol=symbol, entry_dt=date, exit_dt=date,
                entry=entry, exit=exit_p, qty=qty,
                exit_reason=reason, strategy=self.name,
            )

    def signal(self, pit_df: pd.DataFrame, symbol: str) -> "Trade | None":
        """Return a Trade for the last row of *pit_df* if it qualifies, else None.

        *pit_df* must be a point-in-time slice (all rows up to and including
        the day being evaluated), sorted ascending by date.
        """
        df = pit_df.dropna(subset=["Open", "High", "Low", "Close", "Volume"])
        if len(df) < 2:
            return None

        row      = df.iloc[-1]
        prev_row = df.iloc[-2]

        gap_pct = (row["Open"] - prev_row["Close"]) / prev_row["Close"] * 100
        if gap_pct < self.threshold:
            return None

        ema50     = float(df["Close"].ewm(span=50, adjust=False).mean().iloc[-1])
        ema12     = df["Close"].ewm(span=12, adjust=False).mean()
        ema26     = df["Close"].ewm(span=26, adjust=False).mean()
        macd      = ema12 - ema26
        macd_bull = (macd - macd.ewm(span=9, adjust=False).mean()).iloc[-1] > 0
        vol_avg20 = float(df["Volume"].rolling(20).mean().iloc[-1])

        if row["Volume"] < vol_avg20 * 1.5:
            return None
        if prev_row["Close"] < ema50:
            return None
        if not macd_bull:
            return None

        entry  = round(float(row["Open"]) * (1 + SLIPPAGE), 2)
        sl     = round(float(prev_row["Close"]) * 1.002, 2)
        t2     = round(float(row["Open"]) * (1 + gap_pct * 2 / 100), 2)
        t1     = round(float(row["Open"]) * (1 + gap_pct / 100), 2)
        qty    = max(1, int(CAPITAL * POSITION_PCT / entry))

        low, high, close = float(row["Low"]), float(row["High"]), float(row["Close"])

        if low <= sl:
            exit_p, reason = sl, "SL"
        elif high >= t2:
            exit_p, reason = t2, "T2"
        elif high >= t1:
            trail  = round(high * 0.995, 2)
            exit_p = max(trail, close)
            reason = "Trail"
        else:
            exit_p, reason = close, "Close"

        ts = df.index[-1]
        return Trade(
            symbol=symbol, entry_dt=ts, exit_dt=ts,
            entry=entry, exit=exit_p, qty=qty,
            exit_reason=reason, strategy=self.name,
        )


# ── Intraday ML Strategy ──────────────────────────────────────────────────────

class IntradayMLStrategy(Strategy):
    """1h ML-signal strategy using india_intraday_model features."""

    name = "ml_intraday"

    def __init__(self, threshold: float = 0.55,
                 model_path: Path | None = None):
        self.threshold  = threshold
        self.model_path = model_path or Path("stocks_1h/india_intraday_model.pkl")
        self._model     = None
        self._features  = None
        self._nifty     = None
        self._banknifty = None
        self._vix       = None

    def _load(self) -> bool:
        if self._model is not None:
            return True
        if not self.model_path.exists():
            return False
        with open(self.model_path, "rb") as f:
            saved = pickle.load(f)
        self._model    = saved["model"]
        self._features = saved["features"]

        data_dir = self.model_path.parent

        def _s(name: str) -> pd.Series:
            p = data_dir / f"{name}.parquet"
            if not p.exists():
                return pd.Series(dtype=float)
            d = pd.read_parquet(p)
            d.index = pd.to_datetime(d.index, utc=True).tz_localize(None) if d.index.tz else d.index
            return d["Close"]

        self._nifty     = _s("NIFTY_1h")
        self._banknifty = _s("BANKNIFTY_1h")
        self._vix       = _s("VIX_1h")
        return True

    def trades(self, symbol: str) -> Iterator[Trade]:
        if not self._load():
            return

        from models.india_intraday_model import build_features, DATA_DIR
        sym = symbol.replace(".NS", "").replace("-", "_")
        path = DATA_DIR / f"{sym}.parquet"
        if not path.exists():
            return

        df = pd.read_parquet(path)
        df.index = pd.to_datetime(df.index, utc=True).tz_localize(None) if df.index.tz else df.index
        df = df.dropna(subset=["Open", "High", "Low", "Close", "Volume"])
        if len(df) < 200:
            return

        feat  = build_features(df, self._nifty, self._banknifty, self._vix)
        X     = feat[self._features].fillna(0)
        proba = self._model.predict_proba(X)[:, 1]
        close = df["Close"].values
        dates = df.index

        STOP_PCT   = 1.0
        TARGET_PCT = 2.5
        TRAIL_PCT  = 0.5

        i = 0
        while i < len(df) - 1:
            if proba[i] < self.threshold:
                i += 1
                continue

            entry  = close[i] * (1 + SLIPPAGE)
            sl     = entry * (1 - STOP_PCT / 100)
            target = entry * (1 + TARGET_PCT / 100)
            trail  = sl
            qty    = max(1, int(CAPITAL * POSITION_PCT / entry))
            entry_dt = dates[i]

            exit_p = exit_reason = None
            j = i + 1
            while j < len(df):
                price    = close[j]
                same_day = dates[j].date() == entry_dt.date()

                if price > entry * (1 + TRAIL_PCT / 100):
                    new_trail = price * (1 - TRAIL_PCT / 100)
                    if new_trail > trail:
                        trail = new_trail

                if price <= trail:
                    exit_p, exit_reason = trail, "Trail/SL"
                    break
                if price >= target:
                    exit_p, exit_reason = target, "Target"
                    break
                if not same_day or j == len(df) - 1:
                    exit_p, exit_reason = price, "EOD"
                    break
                j += 1

            if exit_p is None:
                i += 1
                continue

            yield Trade(
                symbol=symbol, entry_dt=entry_dt, exit_dt=dates[j],
                entry=round(entry, 2), exit=round(exit_p, 2), qty=qty,
                exit_reason=exit_reason, strategy=self.name,
            )
            i = j + 1


# ── Runner + report ───────────────────────────────────────────────────────────

def run(strategy: Strategy, symbols: list[str]) -> pd.DataFrame:
    """Run strategy over all symbols; return a DataFrame of trades."""
    rows = []
    for sym in symbols:
        for t in strategy.trades(sym):
            rows.append({
                "symbol":      t.symbol,
                "entry_dt":    t.entry_dt,
                "exit_dt":     t.exit_dt,
                "entry":       t.entry,
                "exit":        t.exit,
                "qty":         t.qty,
                "exit_reason": t.exit_reason,
                "pnl_pct":     round(t.pnl_pct, 3),
                "pnl_inr":     round(t.pnl_inr, 2),
                "win":         t.win,
                "strategy":    t.strategy,
            })
    return pd.DataFrame(rows)


def print_report(df: pd.DataFrame, strategy_name: str, params: str = "") -> None:
    if df.empty:
        print("No trades generated.")
        return

    wins   = df[df["win"]]
    losses = df[~df["win"]]
    pf     = (wins["pnl_inr"].sum() / abs(losses["pnl_inr"].sum())
              if len(losses) and losses["pnl_inr"].sum() != 0 else float("inf"))

    SEP = "=" * 65
    print(f"\n{SEP}")
    print(f"  {strategy_name.upper()} BACKTEST  {params}")
    print(f"  Period: {df['entry_dt'].min().date()} → {df['entry_dt'].max().date()}")
    print(SEP)
    print(f"  Total Trades     : {len(df)}")
    print(f"  Win Rate         : {df['win'].mean()*100:.1f}%  ({len(wins)}W / {len(losses)}L)")
    print(f"  Total Net P&L    : ₹{df['pnl_inr'].sum():+,.2f}")
    print(f"  Avg Win          : ₹{wins['pnl_inr'].mean():+.2f}" if len(wins) else "  Avg Win          : —")
    print(f"  Avg Loss         : ₹{losses['pnl_inr'].mean():+.2f}" if len(losses) else "  Avg Loss         : —")
    print(f"  Profit Factor    : {pf:.2f}x")
    print(f"  Worst Trade      : {df['pnl_pct'].min():.2f}%")
    print(SEP)

    print(f"\n  {'SYMBOL':<14} {'TRADES':>7} {'WIN%':>6} {'NET P&L':>10} {'AVG':>8}")
    print(f"  {'-'*50}")
    for sym, grp in df.groupby("symbol"):
        wr  = grp["win"].mean() * 100
        pnl = grp["pnl_inr"].sum()
        avg = grp["pnl_inr"].mean()
        print(f"  {sym:<14} {len(grp):>7} {wr:>5.1f}% {pnl:>+10.2f} {avg:>+8.2f}")

    print(f"\n  Exit Reason Breakdown:")
    for reason, grp in df.groupby("exit_reason"):
        wr = grp["win"].mean() * 100
        print(f"    {reason:<10}: {len(grp):>4} trades | win {wr:.1f}% | avg ₹{grp['pnl_inr'].mean():+.2f}")
    print(f"\n{SEP}\n")


# ── CLI ───────────────────────────────────────────────────────────────────────

def _default_symbols() -> list[str]:
    try:
        import yaml
        with open("config.yaml") as f:
            cfg = yaml.safe_load(f)
        return list(cfg.get("watchlist", []))
    except Exception:
        return []


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Unified backtester")
    parser.add_argument("--strategy", choices=["gap", "ml_intraday"], default="gap")
    parser.add_argument("--threshold", type=float, default=None)
    parser.add_argument("--symbols", nargs="*", default=None)
    args = parser.parse_args()

    symbols = args.symbols or _default_symbols()
    if not symbols:
        print("No symbols. Pass --symbols or set watchlist in config.yaml.")
        raise SystemExit(1)

    if args.strategy == "gap":
        threshold = args.threshold or 2.0
        strategy  = GapStrategy(threshold=threshold)
        params    = f"(gap >= {threshold}%)"
    else:
        threshold = args.threshold or 0.55
        strategy  = IntradayMLStrategy(threshold=threshold)
        params    = f"(threshold={threshold})"

    df = run(strategy, symbols)
    print_report(df, args.strategy, params)
