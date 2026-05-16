"""
Execution Agent — paper trading engine with SQLite trade log.
"""
from __future__ import annotations

import logging
import sqlite3
import uuid
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Optional

import yfinance as yf

from agents.base import Agent, AgentResult

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent.parent / "paper_trades.db"
SLIPPAGE = 0.0005
BROKERAGE = 0.0003  # per side


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id            TEXT PRIMARY KEY,
            symbol        TEXT NOT NULL,
            entry_date    TEXT,
            entry_price   REAL,
            stop_loss     REAL,
            target        REAL,
            position_size REAL,
            exit_date     TEXT,
            exit_price    REAL,
            pnl_pct       REAL,
            pnl_inr       REAL,
            outcome       TEXT DEFAULT 'open',
            reasoning     TEXT,
            signals_json  TEXT,
            created_at    TEXT
        )
    """)
    # Migrate: add signals_json if it doesn't exist yet
    cols = {row[1] for row in conn.execute("PRAGMA table_info(trades)")}
    if "signals_json" not in cols:
        conn.execute("ALTER TABLE trades ADD COLUMN signals_json TEXT")
    conn.commit()
    return conn


def get_period_pnl_pct(days: int) -> float:
    """Return total PNL % for closed trades in the last `days` calendar days."""
    if not DB_PATH.exists():
        return 0.0
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            "SELECT pnl_pct, position_size FROM trades WHERE outcome != 'open' AND exit_date >= ?",
            (cutoff,),
        ).fetchall()
    if not rows:
        return 0.0
    # Weighted average PNL % by position size
    total_size = sum(r[1] for r in rows if r[1])
    if total_size == 0:
        return sum(r[0] for r in rows if r[0]) / len(rows)
    return round(sum(r[0] * r[1] for r in rows if r[0] and r[1]) / total_size, 4)


def _get_ltp(symbol: str) -> float:
    try:
        t = yf.Ticker(symbol + ".NS")
        hist = t.history(period="1d")
        return float(hist["Close"].iloc[-1]) if not hist.empty else 0.0
    except Exception:
        return 0.0


def _pnl(entry: float, exit_: float, position_size: float):
    pnl_pct = (exit_ - entry) / entry * 100 - BROKERAGE * 2 * 100
    pnl_inr = position_size * pnl_pct / 100
    return round(pnl_pct, 4), round(pnl_inr, 2)


class ExecutionAgent(Agent):
    def __init__(self, config: dict):
        super().__init__("ExecutionAgent", config)
        self.mode = config["trading"]["mode"]
        _get_conn().close()  # ensure table exists

    def run(self, context: Optional[dict] = None) -> AgentResult:
        ctx = context or {}
        decision = ctx.get("decision_result", {})
        if decision.get("decision") == "BUY":
            trade = self.execute_trade(
                symbol=decision["symbol"],
                entry_price=decision.get("entry_price", 0),
                stop_loss=decision.get("stop_loss", 0),
                target=decision.get("target", 0),
                position_size=decision.get("position_size", 0),
                reasoning=decision.get("reasoning", ""),
            )
            return self._result(trade)
        return self._result({"status": "no_trade", "decision": decision.get("decision", "HOLD")})

    def execute_trade(self, symbol: str, entry_price: float, stop_loss: float,
                      target: float, position_size: float, reasoning: str = "",
                      signals: dict | None = None) -> dict:
        """Open a new paper trade."""
        if self.mode != "paper":
            raise RuntimeError("Live trading not yet enabled. Set mode=paper in config.")

        import json as _json
        trade_id = str(uuid.uuid4())[:8]
        entry_price_with_slip = round(entry_price * (1 + SLIPPAGE), 2)
        now = datetime.now().isoformat()
        signals_json = _json.dumps(signals) if signals else None

        with _get_conn() as conn:
            conn.execute("""
                INSERT INTO trades (id, symbol, entry_date, entry_price, stop_loss, target,
                                    position_size, outcome, reasoning, signals_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'open', ?, ?, ?)
            """, (trade_id, symbol, now, entry_price_with_slip, stop_loss, target,
                  position_size, reasoning, signals_json, now))

        logger.info(f"Paper trade opened: {trade_id} | {symbol} @ ₹{entry_price_with_slip} | SL ₹{stop_loss} | T ₹{target}")
        return {"trade_id": trade_id, "symbol": symbol, "entry_price": entry_price_with_slip,
                "stop_loss": stop_loss, "target": target, "position_size": position_size}

    def monitor_positions(self) -> list[dict]:
        """Check all open positions against current prices. Close if SL/target hit."""
        closed = []
        with _get_conn() as conn:
            open_trades = conn.execute(
                "SELECT * FROM trades WHERE outcome = 'open'"
            ).fetchall()

        for trade in open_trades:
            symbol = trade["symbol"]
            ltp = _get_ltp(symbol)
            if not ltp:
                continue

            outcome = None
            exit_price = None

            if ltp <= trade["stop_loss"]:
                outcome = "loss"
                exit_price = trade["stop_loss"] * (1 - SLIPPAGE)
            elif ltp >= trade["target"]:
                outcome = "win"
                exit_price = trade["target"] * (1 - SLIPPAGE)

            if outcome:
                pnl_pct, pnl_inr = _pnl(trade["entry_price"], exit_price, trade["position_size"])
                now = datetime.now().isoformat()
                with _get_conn() as conn:
                    conn.execute("""
                        UPDATE trades SET exit_date=?, exit_price=?, pnl_pct=?, pnl_inr=?, outcome=?
                        WHERE id=?
                    """, (now, exit_price, pnl_pct, pnl_inr, outcome, trade["id"]))
                logger.info(f"Trade closed: {trade['id']} | {symbol} | {outcome} | {pnl_pct:+.2f}% | ₹{pnl_inr:+.2f}")
                closed.append({"trade_id": trade["id"], "symbol": symbol, "outcome": outcome,
                                "pnl_pct": pnl_pct, "pnl_inr": pnl_inr})
        return closed

    def emergency_exit(self, symbol: str, reason: str = "emergency") -> Optional[dict]:
        """Immediately close open position for a symbol at market price."""
        ltp = _get_ltp(symbol)
        with _get_conn() as conn:
            trade = conn.execute(
                "SELECT * FROM trades WHERE symbol=? AND outcome='open' LIMIT 1", (symbol,)
            ).fetchone()
            if not trade:
                return None
            exit_price = ltp * (1 - SLIPPAGE)
            pnl_pct, pnl_inr = _pnl(trade["entry_price"], exit_price, trade["position_size"])
            conn.execute("""
                UPDATE trades SET exit_date=?, exit_price=?, pnl_pct=?, pnl_inr=?, outcome='emergency_exit'
                WHERE id=?
            """, (datetime.now().isoformat(), exit_price, pnl_pct, pnl_inr, trade["id"]))
        logger.warning(f"Emergency exit: {symbol} | {reason} | {pnl_pct:+.2f}%")
        return {"symbol": symbol, "pnl_pct": pnl_pct, "pnl_inr": pnl_inr, "reason": reason}

    def daily_report(self) -> dict:
        """Generate end-of-day P&L report."""
        today = date.today().isoformat()
        with _get_conn() as conn:
            trades = conn.execute("""
                SELECT * FROM trades
                WHERE exit_date LIKE ? AND outcome != 'open'
            """, (f"{today}%",)).fetchall()

        if not trades:
            return {"date": today, "trades": 0, "total_pnl_pct": 0.0, "total_pnl_inr": 0.0,
                    "win_rate": 0.0, "best_trade": None, "worst_trade": None}

        pnls = [t["pnl_pct"] for t in trades if t["pnl_pct"] is not None]
        wins = [p for p in pnls if p > 0]
        total_inr = sum(t["pnl_inr"] for t in trades if t["pnl_inr"])

        return {
            "date": today,
            "trades": len(trades),
            "total_pnl_pct": round(sum(pnls), 2),
            "total_pnl_inr": round(total_inr, 2),
            "win_rate": round(len(wins) / len(pnls) * 100, 1) if pnls else 0.0,
            "best_trade": max(pnls) if pnls else None,
            "worst_trade": min(pnls) if pnls else None,
        }


if __name__ == "__main__":
    import yaml
    from dotenv import load_dotenv
    from core.logger import setup_logging

    load_dotenv()
    with open("config.yaml") as f:
        config = yaml.safe_load(f)
    setup_logging(config)

    agent = ExecutionAgent(config)

    # Simulate a BUY trade
    ltp = _get_ltp("RELIANCE")
    trade = agent.execute_trade(
        symbol="RELIANCE",
        entry_price=ltp,
        stop_loss=round(ltp * 0.99, 2),
        target=round(ltp * 1.025, 2),
        position_size=1500.0,
        reasoning="Test trade",
    )
    print(f"\nTrade opened: {trade}")

    report = agent.daily_report()
    print(f"\nDaily Report: {report}")
