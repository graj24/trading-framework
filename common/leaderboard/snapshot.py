"""Cross-PM leaderboard and rival snapshot service.

Reads paper_trades.db filtered by pm_id to compute per-PM stats.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

DB_PATH = Path("paper_trades.db")


def get_pm_stats(pm_id: str, window_days: int = 30) -> dict:
    """Return P&L, win-rate, sharpe, drawdown for a PM over the last N days."""
    if not DB_PATH.exists():
        return _empty_stats(pm_id)
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """SELECT pnl_inr, pnl_pct, exit_date FROM trades
                   WHERE pm_id=? AND outcome!='open'
                   AND exit_date >= datetime('now', ?)
                   ORDER BY exit_date""",
                (pm_id, f"-{window_days} days"),
            ).fetchall()
            open_count = conn.execute(
                "SELECT COUNT(*) FROM trades WHERE pm_id=? AND outcome='open'", (pm_id,)
            ).fetchone()[0]
    except Exception:
        return _empty_stats(pm_id)

    if not rows:
        return _empty_stats(pm_id, open_positions=open_count)

    pnls = [r["pnl_inr"] for r in rows if r["pnl_inr"] is not None]
    pct_pnls = [r["pnl_pct"] for r in rows if r["pnl_pct"] is not None]
    wins = [p for p in pnls if p > 0]
    total_pnl = sum(pnls)
    win_rate = len(wins) / len(pnls) * 100 if pnls else 0

    # Simple Sharpe: mean(pct_returns) / std(pct_returns) * sqrt(252)
    sharpe = 0.0
    if len(pct_pnls) >= 2:
        import statistics
        mean = statistics.mean(pct_pnls)
        std = statistics.stdev(pct_pnls)
        sharpe = round((mean / std) * (252 ** 0.5), 2) if std > 0 else 0.0

    # Max drawdown
    cumulative = 0.0
    peak = 0.0
    max_dd = 0.0
    for p in pnls:
        cumulative += p
        if cumulative > peak:
            peak = cumulative
        dd = peak - cumulative
        if dd > max_dd:
            max_dd = dd

    return {
        "pm_id": pm_id,
        "total_pnl": round(total_pnl, 2),
        "n_trades": len(pnls),
        "win_rate_pct": round(win_rate, 1),
        "sharpe": sharpe,
        "max_drawdown_inr": round(max_dd, 2),
        "open_positions": open_count,
        "window_days": window_days,
    }


def get_leaderboard(window_days: int = 30) -> list[dict]:
    """Return all PMs sorted by total P&L descending."""
    from common.core.pm_runtime import list_pms
    pms = list_pms(active_only=True)
    stats = [get_pm_stats(pm["pm_id"], window_days) for pm in pms]
    return sorted(stats, key=lambda x: x["total_pnl"], reverse=True)


def get_rival_snapshot(self_pm: str, rival_pm: str) -> dict:
    """Return a rival's stats + their last 5 trades."""
    stats = get_pm_stats(rival_pm)
    recent_trades: list[dict] = []
    if DB_PATH.exists():
        try:
            with sqlite3.connect(DB_PATH) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    """SELECT symbol, outcome, pnl_inr, pnl_pct, exit_date
                       FROM trades WHERE pm_id=? AND outcome!='open'
                       ORDER BY exit_date DESC LIMIT 5""",
                    (rival_pm,),
                ).fetchall()
                recent_trades = [dict(r) for r in rows]
        except Exception:
            pass
    return {**stats, "recent_trades": recent_trades}


def _empty_stats(pm_id: str, open_positions: int = 0) -> dict:
    return {
        "pm_id": pm_id,
        "total_pnl": 0.0,
        "n_trades": 0,
        "win_rate_pct": 0.0,
        "sharpe": 0.0,
        "max_drawdown_inr": 0.0,
        "open_positions": open_positions,
        "window_days": 30,
    }
