"""
Event-driven backtesting engine.

Usage:
  python -m core.backtester --stock RELIANCE --strategy rsi
"""
from __future__ import annotations

import argparse
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional

import numpy as np
import pandas as pd

from core.knowledge_base import kb_path

logger = logging.getLogger(__name__)

SLIPPAGE = 0.0005   # 0.05%
BROKERAGE = 0.0003  # 0.03% per side


class SignalType(Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


@dataclass
class Signal:
    type: SignalType
    price: float
    stop_loss: float
    target: float
    confidence: float = 1.0
    reason: str = ""


@dataclass
class Trade:
    symbol: str
    entry_date: datetime
    entry_price: float
    stop_loss: float
    target: float
    exit_date: Optional[datetime] = None
    exit_price: Optional[float] = None
    pnl_pct: Optional[float] = None
    outcome: str = "open"  # win | loss | timeout


@dataclass
class BacktestResult:
    symbol: str
    strategy: str
    trades: list[Trade] = field(default_factory=list)
    # Metrics computed after run
    total_trades: int = 0
    win_rate: float = 0.0
    avg_gain_pct: float = 0.0
    avg_loss_pct: float = 0.0
    expected_value: float = 0.0
    sharpe_ratio: float = 0.0
    max_drawdown_pct: float = 0.0
    total_return_pct: float = 0.0

    def summary(self) -> str:
        return (
            f"\n{'='*55}\n"
            f"  Backtest: {self.symbol} | Strategy: {self.strategy}\n"
            f"{'='*55}\n"
            f"  Trades:        {self.total_trades}\n"
            f"  Win Rate:      {self.win_rate:.1f}%\n"
            f"  Avg Gain:      {self.avg_gain_pct:.2f}%\n"
            f"  Avg Loss:      {self.avg_loss_pct:.2f}%\n"
            f"  Expected Val:  {self.expected_value:.2f}%\n"
            f"  Sharpe Ratio:  {self.sharpe_ratio:.2f}\n"
            f"  Max Drawdown:  {self.max_drawdown_pct:.2f}%\n"
            f"  Total Return:  {self.total_return_pct:.2f}%\n"
            f"{'='*55}"
        )


class Strategy(ABC):
    """Base class for all backtest strategies."""

    @abstractmethod
    def generate_signal(self, df: pd.DataFrame, idx: int) -> Optional[Signal]:
        """Given OHLCV dataframe and current index, return a Signal or None."""


class RSIStrategy(Strategy):
    """
    Simple RSI mean-reversion strategy:
    - Buy when RSI crosses above 30 (oversold recovery)
    - SL: 2× ATR below entry
    - Target: 3× ATR above entry
    """

    def __init__(self, rsi_period: int = 14, atr_period: int = 14):
        self.rsi_period = rsi_period
        self.atr_period = atr_period

    def generate_signal(self, df: pd.DataFrame, idx: int) -> Optional[Signal]:
        if idx < max(self.rsi_period, self.atr_period) + 2:
            return None

        window = df.iloc[: idx + 1]
        rsi = _compute_rsi(window["Close"], self.rsi_period)
        atr = _compute_atr(window, self.atr_period)

        if len(rsi) < 2 or atr == 0:
            return None

        prev_rsi = rsi.iloc[-2]
        curr_rsi = rsi.iloc[-1]
        price = df.iloc[idx]["Close"]

        # RSI crosses above 30 from below
        if prev_rsi < 30 and curr_rsi >= 30:
            sl = price - 2 * atr
            target = price + 3 * atr
            return Signal(
                type=SignalType.BUY,
                price=price,
                stop_loss=sl,
                target=target,
                confidence=0.6,
                reason=f"RSI crossover {prev_rsi:.1f}→{curr_rsi:.1f}",
            )
        return None


class MACDStrategy(Strategy):
    """
    MACD bullish crossover strategy:
    - Buy when MACD line crosses above signal line
    - SL: 1.5× ATR below entry
    - Target: 2.5× ATR above entry
    """

    def generate_signal(self, df: pd.DataFrame, idx: int) -> Optional[Signal]:
        if idx < 35:
            return None

        window = df.iloc[: idx + 1]["Close"]
        macd_line, signal_line = _compute_macd(window)

        if len(macd_line) < 2:
            return None

        atr = _compute_atr(df.iloc[: idx + 1], 14)
        price = df.iloc[idx]["Close"]

        if macd_line.iloc[-2] < signal_line.iloc[-2] and macd_line.iloc[-1] >= signal_line.iloc[-1]:
            sl = price - 1.5 * atr
            target = price + 2.5 * atr
            return Signal(
                type=SignalType.BUY,
                price=price,
                stop_loss=sl,
                target=target,
                confidence=0.65,
                reason="MACD bullish crossover",
            )
        return None


class Backtester:
    """Event-driven backtester with slippage and brokerage simulation."""

    def __init__(self, slippage: float = SLIPPAGE, brokerage: float = BROKERAGE):
        self.slippage = slippage
        self.brokerage = brokerage

    def run(
        self,
        symbol: str,
        strategy: Strategy,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        walk_forward_splits: int = 1,
    ) -> BacktestResult:
        """Run backtest on stored price history."""
        path = kb_path(symbol) / "price_history.parquet"
        if not path.exists():
            raise FileNotFoundError(f"No price history for {symbol}. Run DataAgent first.")

        df = pd.read_parquet(path).sort_index()
        df.index = pd.to_datetime(df.index, utc=True).tz_localize(None)

        if start_date:
            df = df[df.index >= start_date]
        if end_date:
            df = df[df.index <= end_date]

        if walk_forward_splits > 1:
            return self._walk_forward(symbol, strategy, df, walk_forward_splits)

        result = self._run_on_df(symbol, strategy.__class__.__name__, df)
        _compute_metrics(result)
        return result

    def _run_on_df(self, symbol: str, strategy_name: str, df: pd.DataFrame) -> BacktestResult:
        result = BacktestResult(symbol=symbol, strategy=strategy_name)
        strategy = _strategy_from_name(strategy_name)
        open_trade: Optional[Trade] = None

        for i in range(len(df)):
            row = df.iloc[i]
            date = df.index[i]
            high = row["High"]
            low = row["Low"]
            close = row["Close"]

            # Manage open trade
            if open_trade is not None:
                # Check SL hit
                if low <= open_trade.stop_loss:
                    exit_price = open_trade.stop_loss * (1 - self.slippage)
                    open_trade.exit_date = date
                    open_trade.exit_price = exit_price
                    open_trade.pnl_pct = _pnl(open_trade.entry_price, exit_price, self.brokerage)
                    open_trade.outcome = "loss"
                    result.trades.append(open_trade)
                    open_trade = None
                    continue

                # Check target hit
                if high >= open_trade.target:
                    exit_price = open_trade.target * (1 - self.slippage)
                    open_trade.exit_date = date
                    open_trade.exit_price = exit_price
                    open_trade.pnl_pct = _pnl(open_trade.entry_price, exit_price, self.brokerage)
                    open_trade.outcome = "win"
                    result.trades.append(open_trade)
                    open_trade = None
                    continue

                # Timeout after 20 bars
                entry_idx = df.index.get_loc(open_trade.entry_date)
                if i - entry_idx >= 20:
                    open_trade.exit_date = date
                    open_trade.exit_price = close * (1 - self.slippage)
                    open_trade.pnl_pct = _pnl(open_trade.entry_price, open_trade.exit_price, self.brokerage)
                    open_trade.outcome = "timeout"
                    result.trades.append(open_trade)
                    open_trade = None
                continue

            # Look for new signal
            signal = strategy.generate_signal(df, i)
            if signal and signal.type == SignalType.BUY:
                entry_price = signal.price * (1 + self.slippage)
                open_trade = Trade(
                    symbol=symbol,
                    entry_date=date,
                    entry_price=entry_price,
                    stop_loss=signal.stop_loss,
                    target=signal.target,
                )

        # Close any open trade at end
        if open_trade is not None:
            last_close = df.iloc[-1]["Close"]
            open_trade.exit_date = df.index[-1]
            open_trade.exit_price = last_close
            open_trade.pnl_pct = _pnl(open_trade.entry_price, last_close, self.brokerage)
            open_trade.outcome = "timeout"
            result.trades.append(open_trade)

        return result

    def _walk_forward(
        self, symbol: str, strategy: Strategy, df: pd.DataFrame, splits: int
    ) -> BacktestResult:
        """Walk-forward validation: train on first 70%, test on last 30% of each split."""
        chunk_size = len(df) // splits
        all_trades: list[Trade] = []
        strategy_name = strategy.__class__.__name__

        for i in range(splits):
            start = i * chunk_size
            end = start + chunk_size
            test_start = start + int(chunk_size * 0.7)
            test_df = df.iloc[test_start:end]
            if len(test_df) < 30:
                continue
            partial = self._run_on_df(symbol, strategy_name, test_df)
            all_trades.extend(partial.trades)

        result = BacktestResult(symbol=symbol, strategy=f"{strategy_name}(WF×{splits})", trades=all_trades)
        _compute_metrics(result)
        return result


# ── Helpers ──────────────────────────────────────────────────────────────────

def _compute_rsi(series: pd.Series, period: int) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _compute_atr(df: pd.DataFrame, period: int) -> float:
    high = df["High"]
    low = df["Low"]
    close = df["Close"]
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs(),
    ], axis=1).max(axis=1)
    atr = tr.rolling(period).mean()
    return float(atr.iloc[-1]) if not atr.empty and not np.isnan(atr.iloc[-1]) else 0.0


def _compute_macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    ema_fast = series.ewm(span=fast, adjust=False).mean()
    ema_slow = series.ewm(span=slow, adjust=False).mean()
    macd = ema_fast - ema_slow
    signal_line = macd.ewm(span=signal, adjust=False).mean()
    return macd, signal_line


def _pnl(entry: float, exit_: float, brokerage: float) -> float:
    gross = (exit_ - entry) / entry * 100
    cost = brokerage * 2 * 100  # both sides
    return round(gross - cost, 4)


def _strategy_from_name(name: str) -> Strategy:
    strategies = {
        "RSIStrategy": RSIStrategy(),
        "MACDStrategy": MACDStrategy(),
        "rsi": RSIStrategy(),
        "macd": MACDStrategy(),
    }
    if name not in strategies:
        raise ValueError(f"Unknown strategy: {name}. Available: {list(strategies.keys())}")
    return strategies[name]


def _compute_metrics(result: BacktestResult) -> None:
    trades = result.trades
    result.total_trades = len(trades)
    if not trades:
        return

    pnls = [t.pnl_pct for t in trades if t.pnl_pct is not None]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]

    result.win_rate = len(wins) / len(pnls) * 100 if pnls else 0
    result.avg_gain_pct = sum(wins) / len(wins) if wins else 0
    result.avg_loss_pct = sum(losses) / len(losses) if losses else 0
    result.expected_value = (
        (result.win_rate / 100) * result.avg_gain_pct
        + (1 - result.win_rate / 100) * result.avg_loss_pct
    )
    result.total_return_pct = sum(pnls)

    # Sharpe ratio (annualised, assuming daily trades)
    if len(pnls) > 1:
        arr = np.array(pnls)
        result.sharpe_ratio = float(arr.mean() / arr.std() * np.sqrt(252)) if arr.std() > 0 else 0.0

    # Max drawdown
    equity = np.cumsum(pnls)
    peak = np.maximum.accumulate(equity)
    drawdown = equity - peak
    result.max_drawdown_pct = float(drawdown.min()) if len(drawdown) > 0 else 0.0


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import yaml
    from dotenv import load_dotenv
    from core.logger import setup_logging

    load_dotenv()
    with open("config.yaml") as f:
        config = yaml.safe_load(f)
    setup_logging(config)

    parser = argparse.ArgumentParser()
    parser.add_argument("--stock", default="RELIANCE")
    parser.add_argument("--strategy", default="rsi", choices=["rsi", "macd"])
    parser.add_argument("--start", default="2020-01-01")
    parser.add_argument("--end", default=None)
    parser.add_argument("--walk-forward", type=int, default=1)
    args = parser.parse_args()

    bt = Backtester()
    strategy = _strategy_from_name(args.strategy)
    result = bt.run(
        symbol=args.stock,
        strategy=strategy,
        start_date=args.start,
        end_date=args.end,
        walk_forward_splits=args.walk_forward,
    )
    print(result.summary())

    if result.trades:
        print(f"\n  Last 5 trades:")
        for t in result.trades[-5:]:
            print(f"    {t.entry_date.date()} → {t.exit_date.date() if t.exit_date else '?'} | "
                  f"{t.outcome:8s} | {t.pnl_pct:+.2f}%")
