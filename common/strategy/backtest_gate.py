"""Backtest gate for strategy evolution.

Runs a simplified backtest on a strategy's watchlist using the existing
GapStrategy as a proxy (it's the fastest, data-only, no live calls).
Returns Sharpe ratio so the strategist can gate on improvement.

If data is unavailable for a symbol it's skipped gracefully.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_STOCKS_DIR = Path(__file__).resolve().parent.parent.parent / "stocks"


def backtest_strategy(strategy: dict[str, Any], capital: float = 10_000) -> dict:
    """Run a quick backtest on the strategy's watchlist.

    Returns:
        {sharpe, total_pnl, n_trades, win_rate, symbols_tested}
    """
    watchlist = strategy.get("watchlist", [])
    if not watchlist:
        return {"sharpe": 0.0, "total_pnl": 0.0, "n_trades": 0, "win_rate": 0.0, "symbols_tested": 0}

    try:
        from core.backtester import GapStrategy, BacktestResult
    except Exception as e:
        logger.warning(f"Backtester import failed: {e}")
        return {"sharpe": 0.0, "total_pnl": 0.0, "n_trades": 0, "win_rate": 0.0, "symbols_tested": 0}

    all_trades = []
    tested = 0
    for symbol in watchlist[:10]:  # cap at 10 to keep it fast
        data_path = _STOCKS_DIR / symbol / "price_history.parquet"
        if not data_path.exists():
            continue
        try:
            import pandas as pd
            df = pd.read_parquet(data_path)
            strat = GapStrategy(threshold=2.0, capital=capital)
            result: BacktestResult = strat.run([symbol])
            all_trades.extend(result.trades)
            tested += 1
        except Exception as e:
            logger.debug(f"Backtest skipped {symbol}: {e}")

    if not all_trades:
        return {"sharpe": 0.0, "total_pnl": 0.0, "n_trades": 0, "win_rate": 0.0, "symbols_tested": tested}

    pnls = [t.pnl_inr for t in all_trades]
    total_pnl = sum(pnls)
    wins = [p for p in pnls if p > 0]
    win_rate = len(wins) / len(pnls) * 100 if pnls else 0

    sharpe = 0.0
    if len(pnls) >= 2:
        import statistics
        mean = statistics.mean(pnls)
        std = statistics.stdev(pnls)
        sharpe = round((mean / std) * (252 ** 0.5), 2) if std > 0 else 0.0

    return {
        "sharpe": sharpe,
        "total_pnl": round(total_pnl, 2),
        "n_trades": len(pnls),
        "win_rate": round(win_rate, 1),
        "symbols_tested": tested,
    }
