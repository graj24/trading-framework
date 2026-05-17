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

from common.agents.base import Agent, AgentResult
from common.core.costs import SLIPPAGE_FRAC as SLIPPAGE, BROKERAGE_FRAC as BROKERAGE

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent.parent.parent / "paper_trades.db"


def migrate_trades_schema(db_path=None) -> None:
    """Idempotently ensure the trades table exists with all required columns."""
    path = db_path or DB_PATH
    conn = sqlite3.connect(path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id               TEXT PRIMARY KEY,
            symbol           TEXT NOT NULL,
            entry_date       TEXT,
            entry_price      REAL,
            stop_loss        REAL,
            target           REAL,
            position_size    REAL,
            exit_date        TEXT,
            exit_price       REAL,
            pnl_pct          REAL,
            pnl_inr          REAL,
            outcome          TEXT DEFAULT 'open',
            reasoning        TEXT,
            signals_json     TEXT,
            created_at       TEXT,
            pm_id            TEXT,
            technical_score  REAL,
            sentiment        REAL,
            pattern_ev       REAL,
            sector_momentum  REAL,
            regime_alignment REAL,
            weights_applied  INTEGER DEFAULT 0,
            signal_source    TEXT
        )
    """)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(trades)")}
    for col, typedef in [
        ("signals_json",     "TEXT"),
        ("pm_id",            "TEXT"),
        ("technical_score",  "REAL"),
        ("sentiment",        "REAL"),
        ("pattern_ev",       "REAL"),
        ("sector_momentum",  "REAL"),
        ("regime_alignment", "REAL"),
        ("weights_applied",  "INTEGER DEFAULT 0"),
        ("signal_source",    "TEXT"),
    ]:
        if col not in cols:
            conn.execute(f"ALTER TABLE trades ADD COLUMN {col} {typedef}")
    conn.commit()
    conn.close()


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    migrate_trades_schema(DB_PATH)
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


def get_open_position_symbols(db_path=None) -> list[str]:
    """Return list of symbols with currently open positions."""
    path = db_path or DB_PATH
    if not Path(path).exists():
        return []
    with sqlite3.connect(path) as conn:
        return [r[0] for r in conn.execute(
            "SELECT symbol FROM trades WHERE outcome='open'"
        ).fetchall()]


def today_pnl_pct(capital: float = 0, db_path=None) -> float:
    """Return today's realised PNL as a fraction of capital (or 0 if capital=0)."""
    if capital == 0:
        return 0.0
    path = db_path or DB_PATH
    if not Path(path).exists():
        return 0.0
    today_prefix = date.today().isoformat()  # "2026-05-17"
    with sqlite3.connect(path) as conn:
        rows = conn.execute(
            "SELECT pnl_inr FROM trades WHERE outcome != 'open' AND exit_date >= ?",
            (today_prefix,),
        ).fetchall()
    total_inr = sum(r[0] for r in rows if r[0] is not None)
    return round(total_inr / capital * 100, 6)


def fetch_unweighted_closed_trades(db_path=None) -> list[dict]:
    """Return closed trades where weights have not yet been applied."""
    path = db_path or DB_PATH
    if not Path(path).exists():
        return []
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM trades WHERE outcome != 'open' AND weights_applied = 0"
        ).fetchall()
    return [dict(r) for r in rows]


def mark_trade_weights_applied(db_path, trade_id: str) -> None:
    """Mark a trade as having had its weights applied."""
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE trades SET weights_applied = 1 WHERE id = ?", (trade_id,))


def _get_ltp(symbol: str) -> float:
    # Try Groww first
    try:
        from common.core.groww_client import get_groww_client
        client = get_groww_client()
        prices = client.get_ltp([symbol])
        if prices and symbol in prices and prices[symbol]:
            return float(prices[symbol])
    except Exception:
        pass
    # Fall back to yfinance
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
                      signals: dict | None = None,
                      signals_at_entry: dict | None = None,
                      pm_id: str | None = None) -> dict:
        """Open a new trade (paper or live)."""
        sigs = signals_at_entry or signals or {}
        import json as _json

        trade_id = str(uuid.uuid4())[:8]
        entry_price_with_slip = round(entry_price * (1 + SLIPPAGE), 2)
        now = datetime.now().isoformat()
        signals_json = _json.dumps(sigs) if sigs else None

        broker_order_id = None
        if self.mode == "live":
            from common.core.broker import get_broker
            broker = get_broker(self.config)
            qty = max(1, int(position_size / entry_price_with_slip))
            broker_order_id = broker.place_order(
                symbol=symbol, qty=qty, order_type="MARKET",
                price=0, sl=stop_loss, tag=f"pm{pm_id or ''}_live",
                pm_id=pm_id or "",
            )

        migrate_trades_schema(DB_PATH)
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("""
                INSERT INTO trades (id, symbol, entry_date, entry_price, stop_loss, target,
                                    position_size, outcome, reasoning, signals_json, created_at,
                                    pm_id, technical_score, sentiment, pattern_ev,
                                    sector_momentum, regime_alignment, weights_applied, signal_source)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'open', ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
            """, (
                trade_id, symbol, now, entry_price_with_slip, stop_loss, target,
                position_size, reasoning, signals_json, now,
                pm_id,
                sigs.get("technical_score"),
                sigs.get("sentiment"),
                sigs.get("pattern_ev"),
                sigs.get("sector_momentum"),
                sigs.get("regime_alignment"),
                sigs.get("signal_source"),
            ))

        logger.info(f"Trade opened: {trade_id} | {symbol} @ ₹{entry_price_with_slip} | SL ₹{stop_loss} | T ₹{target}")
        result = {"trade_id": trade_id, "symbol": symbol, "entry_price": entry_price_with_slip,
                  "stop_loss": stop_loss, "target": target, "position_size": position_size}
        if broker_order_id:
            result["broker_order_id"] = broker_order_id
        return result

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

    def signal_attribution(self, since_date: str | None = None) -> list[dict]:
        """P2 §19: Group closed trades by signal_source and compute per-source stats.

        Args:
            since_date: ISO date string (e.g. "2026-01-01"). If None, uses all history.

        Returns a list of dicts, one per signal_source, sorted by total P&L descending:
            {signal_source, trades, win_rate, total_pnl_inr, avg_pnl_pct, avg_pnl_inr}
        """
        query = "SELECT * FROM trades WHERE outcome != 'open'"
        params: tuple = ()
        if since_date:
            query += " AND exit_date >= ?"
            params = (since_date,)

        with _get_conn() as conn:
            rows = conn.execute(query, params).fetchall()

        if not rows:
            return []

        # Group by signal_source (NULL → "unknown")
        groups: dict[str, list] = {}
        for row in rows:
            src = (row["signal_source"] if row["signal_source"] is not None else "unknown") if "signal_source" in row.keys() else "unknown"
            groups.setdefault(src, []).append(row)

        result = []
        for src, trades in groups.items():
            pnl_pcts = [t["pnl_pct"] for t in trades if t["pnl_pct"] is not None]
            pnl_inrs = [t["pnl_inr"] for t in trades if t["pnl_inr"] is not None]
            wins     = [p for p in pnl_pcts if p > 0]
            result.append({
                "signal_source":  src,
                "trades":         len(trades),
                "win_rate":       round(len(wins) / len(pnl_pcts) * 100, 1) if pnl_pcts else 0.0,
                "total_pnl_inr":  round(sum(pnl_inrs), 2),
                "avg_pnl_pct":    round(sum(pnl_pcts) / len(pnl_pcts), 3) if pnl_pcts else 0.0,
                "avg_pnl_inr":    round(sum(pnl_inrs) / len(pnl_inrs), 2) if pnl_inrs else 0.0,
            })

        return sorted(result, key=lambda x: x["total_pnl_inr"], reverse=True)


if __name__ == "__main__":
    import yaml
    from dotenv import load_dotenv
    from common.core.logger import setup_logging

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
