"""
Execution Agent — paper trading engine with SQLite trade log.
"""
from __future__ import annotations

import logging
import sqlite3
import uuid
from datetime import datetime, date
from pathlib import Path
from typing import Optional

import yfinance as yf

from agents.base import Agent, AgentResult

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent.parent / "paper_trades.db"

# Cost constants come from the canonical core.costs module so that backtest
# numbers and live paper P&L are directly comparable. See
# docs-verification/findings.md MED-7.
from core.costs import SLIPPAGE_FRAC as SLIPPAGE
from core.costs import BROKERAGE_FRAC as BROKERAGE


# Columns added by CRIT-2 (additive nullable columns; safe to ALTER on
# existing DBs). Order matters only for the INSERT below.
SIGNAL_COLUMNS: tuple[str, ...] = (
    "technical_score",
    "sentiment",
    "pattern_ev",
    "sector_momentum",
    "regime_alignment",
)


def migrate_trades_schema(db_path: Path | str = DB_PATH) -> None:
    """Idempotent migration that brings the trades table up to the
    post-CRIT-2 schema.

    Safe to call on:
    * a missing DB file        — creates the file + table.
    * a legacy schema          — adds nullable columns one by one.
    * an already-migrated DB   — no-op (sqlite3 ignores duplicate ALTERs
                                  caught here).
    """
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
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
            created_at    TEXT
        )
        """
    )

    existing = {row[1] for row in conn.execute("PRAGMA table_info(trades)").fetchall()}
    additions: list[tuple[str, str]] = [
        ("technical_score",  "REAL"),
        ("sentiment",        "REAL"),
        ("pattern_ev",       "REAL"),
        ("sector_momentum",  "REAL"),
        ("regime_alignment", "REAL"),
        ("weights_applied",  "INTEGER DEFAULT 0"),
        ("signal_source",    "TEXT"),          # P2 §19: gap|pattern|ml_daily|ml_intraday|…
    ]
    for col, typ in additions:
        if col not in existing:
            conn.execute(f"ALTER TABLE trades ADD COLUMN {col} {typ}")
    conn.commit()
    conn.close()


def fetch_unweighted_closed_trades(db_path: Path | str = DB_PATH) -> list[sqlite3.Row]:
    """Return closed trades where the LearningAgent has not yet been applied.

    Used by the post-cycle learning loop in `main.py` (after CRIT-2 +
    Issue B2 fix) so we update each trade's weights exactly once.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT * FROM trades
        WHERE outcome != 'open'
          AND COALESCE(weights_applied, 0) = 0
        """
    ).fetchall()
    conn.close()
    return list(rows)


def mark_trade_weights_applied(db_path: Path | str, trade_id: str) -> None:
    """Set weights_applied = 1 for the given trade, so the learning loop
    won't re-apply on the next run."""
    conn = sqlite3.connect(db_path)
    conn.execute(
        "UPDATE trades SET weights_applied = 1 WHERE id = ?", (trade_id,)
    )
    conn.commit()
    conn.close()


def get_open_position_symbols(db_path: Path | str | None = None) -> list[str]:
    """Return the symbols of every position currently in 'open' state.

    Used by `MasterAgent.run_for_stock` to feed the RiskManager's correlation
    and sector-overlap gates with real data (Issue B1 / HIGH-5). Returns ``[]``
    when the DB is missing or the table is empty.
    """
    p = Path(db_path) if db_path is not None else Path(DB_PATH)
    if not p.exists():
        return []
    migrate_trades_schema(p)  # ensure the table exists
    conn = sqlite3.connect(p)
    rows = conn.execute(
        "SELECT DISTINCT symbol FROM trades WHERE outcome = 'open'"
    ).fetchall()
    conn.close()
    return [r[0] for r in rows]


def today_pnl_pct(capital: float, db_path: Path | str | None = None) -> float:
    """Today's realised P&L as a percentage of capital.

    Sums ``pnl_inr`` across all closed trades whose ``exit_date`` falls on
    today (matching `daily_report` semantics: closed-only). Used by the
    risk-manager's daily-loss circuit breaker.
    """
    if not capital:
        return 0.0
    p = Path(db_path) if db_path is not None else Path(DB_PATH)
    if not p.exists():
        return 0.0
    today = date.today().isoformat()
    conn = sqlite3.connect(p)
    rows = conn.execute(
        """
        SELECT pnl_inr FROM trades
        WHERE outcome != 'open'
          AND exit_date LIKE ?
        """,
        (f"{today}%",),
    ).fetchall()
    conn.close()
    inr_sum = sum(r[0] for r in rows if r[0] is not None)
    return round(inr_sum / capital * 100, 6)


def _get_conn() -> sqlite3.Connection:
    """Open the trade-ledger DB. Schema is brought up to date automatically.
    Returns a connection with `row_factory = sqlite3.Row`.

    B.5 / Issue C9: enables WAL mode so concurrent readers (e.g. the
    dashboard) don't block writers (e.g. monitor_positions).
    """
    migrate_trades_schema(DB_PATH)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    # Enable WAL — idempotent; safe to call on every open.
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
    except sqlite3.DatabaseError:
        # Some hosts (e.g. read-only filesystems) reject WAL — fall back
        # to default journal mode quietly.
        pass
    return conn


def _get_ltp(symbol: str) -> float:
    """Fetch the live LTP for ``symbol``.

    C.2 / Issue B6: prefer Groww (real intraday LTP, batch-friendly) and
    fall back to yfinance (returns previous close outside market hours).
    Returns 0.0 if both providers fail.
    """
    # 1. Try Groww first.
    try:
        from core.groww_client import get_groww_client
        client = get_groww_client()
        ltps = client.get_ltp([symbol])
        if ltps and ltps.get(symbol):
            return float(ltps[symbol])
    except Exception:
        pass  # fall through to yfinance

    # 2. Fall back to yfinance.
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
        # C.3: route order placement through the Broker abstraction so
        # `mode=live` becomes a functional path. Lazily instantiated to
        # avoid importing kiteconnect on paper-only systems.
        self._broker = None

    def _get_broker(self):
        """Lazily build the broker matching `config.trading.mode`."""
        if self._broker is None:
            from core.broker import get_broker
            self._broker = get_broker(self.config)
        return self._broker

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
                      signals_at_entry: Optional[dict] = None) -> dict:
        """Open a new trade.

        In ``paper`` mode (default), the trade is recorded only in the
        SQLite ledger.

        In ``live`` mode (C.3), the order is also placed via the
        ``Broker`` abstraction (Zerodha by default). The SQLite row is
        still written so the dashboard / learning loop / monitor see one
        consistent ledger.

        Args:
            signals_at_entry: optional dict with any of
                ``technical_score, sentiment, pattern_ev, sector_momentum,
                regime_alignment``. Persisted into the matching columns so
                the LearningAgent has real values to reason about. Missing
                keys are stored as NULL.
        """
        trade_id = str(uuid.uuid4())[:8]
        entry_price_with_slip = round(entry_price * (1 + SLIPPAGE), 2)
        now = datetime.now().isoformat()
        sigs = signals_at_entry or {}

        broker_order_id: str | None = None
        if self.mode in ("live", "shadow"):
            try:
                qty = max(1, int(position_size / entry_price_with_slip)) if entry_price_with_slip else 1
                broker = self._get_broker()
                broker_order_id = broker.place_order(
                    symbol=symbol,
                    qty=qty,
                    order_type="MARKET",
                    price=0.0,
                    sl=stop_loss,
                    tag=f"trade-{trade_id}",
                )
                logger.info(f"{'Live' if self.mode == 'live' else 'Shadow'} order placed: {broker_order_id} | {symbol} qty={qty}")
            except Exception as e:
                logger.error(f"Order placement failed for {symbol}: {e}")
                if self.mode == "live":
                    raise  # shadow mode continues even if live leg fails

        with _get_conn() as conn:
            conn.execute(
                """
                INSERT INTO trades (
                    id, symbol, entry_date, entry_price, stop_loss, target,
                    position_size, outcome, reasoning, created_at,
                    technical_score, sentiment, pattern_ev,
                    sector_momentum, regime_alignment, weights_applied,
                    signal_source
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'open', ?, ?, ?, ?, ?, ?, ?, 0, ?)
                """,
                (
                    trade_id, symbol, now, entry_price_with_slip, stop_loss, target,
                    position_size, reasoning, now,
                    sigs.get("technical_score"),
                    sigs.get("sentiment"),
                    sigs.get("pattern_ev"),
                    sigs.get("sector_momentum"),
                    sigs.get("regime_alignment"),
                    sigs.get("signal_source"),
                ),
            )

        mode_tag = {"live": "Live", "shadow": "Shadow"}.get(self.mode, "Paper")
        logger.info(f"{mode_tag} trade opened: {trade_id} | {symbol} @ ₹{entry_price_with_slip} | SL ₹{stop_loss} | T ₹{target}")
        out = {"trade_id": trade_id, "symbol": symbol, "entry_price": entry_price_with_slip,
               "stop_loss": stop_loss, "target": target, "position_size": position_size}
        if broker_order_id:
            out["broker_order_id"] = broker_order_id
        return out

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
            src = (row["signal_source"] or "unknown") if "signal_source" in row.keys() else "unknown"
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
