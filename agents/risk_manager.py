"""Risk Manager agent – position sizing, stop losses, and portfolio rules."""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import pandas as pd
import yaml

from agents.base import Agent, AgentResult, AgentStatus

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = BASE_DIR / "config.yaml"
AUDIT_LOG_PATH = BASE_DIR / "risk_audit.jsonl"

logger = logging.getLogger(__name__)


def _load_config() -> dict:
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


# ── Audit log ─────────────────────────────────────────────────────────────────

def audit_log(pm_id: str, event: str, detail: dict):
    """Append one line to the risk audit log."""
    entry = {
        "ts": datetime.utcnow().isoformat(),
        "pm_id": pm_id,
        "event": event,
        **detail,
    }
    with AUDIT_LOG_PATH.open("a") as f:
        f.write(json.dumps(entry) + "\n")


# ── Circuit breaker ───────────────────────────────────────────────────────────

def _get_pm_pnl(pm_id: str, db_path: str = "paper_trades.db") -> dict:
    """Return daily and weekly realised P&L pct for a PM."""
    db = BASE_DIR / db_path
    if not db.exists():
        return {"daily_pct": 0.0, "weekly_pct": 0.0}
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        today = date.today().isoformat()
        daily = conn.execute(
            "SELECT COALESCE(SUM(pnl_inr),0) FROM trades "
            "WHERE pm_id=? AND outcome!='open' AND exit_date LIKE ?",
            (pm_id, f"{today}%"),
        ).fetchone()[0]
        weekly = conn.execute(
            "SELECT COALESCE(SUM(pnl_inr),0) FROM trades "
            "WHERE pm_id=? AND outcome!='open' AND exit_date >= date('now','-7 days')",
            (pm_id,),
        ).fetchone()[0]
        capital_row = conn.execute(
            "SELECT COALESCE(SUM(entry_price*quantity),1) FROM trades WHERE pm_id=?",
            (pm_id,),
        ).fetchone()[0] or 1
    return {
        "daily_pct": daily / capital_row * 100,
        "weekly_pct": weekly / capital_row * 100,
    }


def check_circuit_breaker(pm_id: str, config: dict | None = None) -> tuple[bool, str]:
    """
    Returns (trading_allowed, reason).
    Publishes risk.breach event and activates kill switch on severe breach.
    """
    cfg = (config or _load_config()).get("risk", {})
    daily_halt = cfg.get("max_loss_per_day_pct", 3.0)
    weekly_halve = cfg.get("max_loss_per_week_pct", 7.0)

    pnl = _get_pm_pnl(pm_id)
    daily_pct = pnl["daily_pct"]
    weekly_pct = pnl["weekly_pct"]

    if daily_pct <= -daily_halt:
        reason = f"Daily loss {daily_pct:.1f}% exceeds -{daily_halt}% — PM{pm_id} halted"
        audit_log(pm_id, "DAILY_HALT", {"daily_pct": daily_pct, "limit": daily_halt})
        try:
            from core.event_bus import get_bus
            get_bus().publish(f"risk.breach.{pm_id}", {"reason": reason, "daily_pct": daily_pct}, pm_id=pm_id, severity="CRITICAL")
        except Exception:
            pass
        return False, reason

    if weekly_pct <= -weekly_halve:
        reason = f"Weekly loss {weekly_pct:.1f}% exceeds -{weekly_halve}% — PM{pm_id} sizes halved"
        audit_log(pm_id, "WEEKLY_HALVE", {"weekly_pct": weekly_pct, "limit": weekly_halve})
        return True, reason  # allowed but caller should halve sizes

    return True, "OK"


# --- 1. Kelly Criterion (half-Kelly) ---

def kelly_size(win_rate: float, avg_win_pct: float, avg_loss_pct: float, capital: float, kelly_fraction: float = 0.5) -> float:
    if avg_loss_pct == 0 or avg_win_pct == 0:
        return capital * 0.1  # default 10% if no data
    b = avg_win_pct / abs(avg_loss_pct)
    if b == 0:
        return 0.0
    p = win_rate / 100
    q = 1 - p
    kelly = (b * p - q) / b
    fraction = max(0, kelly * kelly_fraction)
    return capital * fraction


# --- 2. ATR-based dynamic stop loss ---

def compute_atr(symbol: str, period: int = 14) -> float:
    path = BASE_DIR / "stocks" / symbol / "price_history.parquet"
    df = pd.read_parquet(path)
    df = df.sort_values("Date").tail(period + 1).reset_index(drop=True)
    high = df["High"]
    low = df["Low"]
    close = df["Close"].shift(1)
    tr = pd.concat([high - low, (high - close).abs(), (low - close).abs()], axis=1).max(axis=1)
    return tr.iloc[1:].mean()


def atr_stop_loss(entry_price: float, symbol: str, atr_multiplier: float = 2.0) -> float:
    atr = compute_atr(symbol)
    return entry_price - (atr_multiplier * atr)


# --- 3. Trailing stop ---

def trailing_stop(current_price: float, entry_price: float, current_stop: float,
                  activate_after_pct: float = 1.0, trail_distance_pct: float = 0.5) -> float:
    profit_pct = ((current_price - entry_price) / entry_price) * 100
    if profit_pct >= activate_after_pct:
        new_stop = current_price * (1 - trail_distance_pct / 100)
        return max(current_stop, new_stop)
    return current_stop


# --- 4. Loss limit checks ---

def check_trade_allowed(daily_pnl_pct: float, weekly_pnl_pct: float = 0.0,
                        monthly_pnl_pct: float = 0.0) -> tuple[bool, str]:
    cfg = _load_config()["risk"]
    if daily_pnl_pct <= -cfg.get("max_loss_per_day_pct", 3.0):
        return False, f"Daily loss {daily_pnl_pct:.1f}% exceeds limit – trading stopped"
    if weekly_pnl_pct <= -cfg.get("max_loss_per_week_pct", 7.0):
        return True, f"Weekly loss {weekly_pnl_pct:.1f}% – reducing sizes 50%"
    if monthly_pnl_pct <= -cfg.get("max_loss_per_month_pct", 15.0):
        return True, f"Monthly loss {monthly_pnl_pct:.1f}% – switching to paper"
    return True, "OK"


# --- 5. Portfolio rules ---

def check_correlation(symbol: str, open_positions: list[str]) -> tuple[bool, str]:
    sym_path = BASE_DIR / "stocks" / symbol / "sector_correlation.json"
    if not sym_path.exists():
        return True, "No correlation data"
    with open(sym_path) as f:
        sym_corr = json.load(f)["correlations"]
    for pos in open_positions:
        pos_path = BASE_DIR / "stocks" / pos / "sector_correlation.json"
        if not pos_path.exists():
            continue
        with open(pos_path) as f:
            pos_corr = json.load(f)["correlations"]
        # Compare correlation vectors
        common = set(sym_corr) & set(pos_corr)
        if common:
            avg = sum(sym_corr[k] * pos_corr[k] for k in common) / len(common)
            if avg > 0.8:
                return False, f"High correlation ({avg:.2f}) with {pos}"
    return True, "OK"


def check_sector_overlap(symbol: str, open_positions: list[str]) -> tuple[bool, str]:
    fund_path = BASE_DIR / "stocks" / symbol / "fundamentals.json"
    if not fund_path.exists():
        return True, "No fundamentals data"
    with open(fund_path) as f:
        sector = json.load(f).get("sector", "")
    count = 0
    for pos in open_positions:
        p = BASE_DIR / "stocks" / pos / "fundamentals.json"
        if not p.exists():
            continue
        with open(p) as f:
            if json.load(f).get("sector", "") == sector:
                count += 1
    cfg = _load_config()["risk"]
    max_pos = cfg.get("max_open_positions", 3)
    if count >= 2:
        return False, f"Sector '{sector}' already has {count} positions (max 2)"
    if len(open_positions) >= max_pos:
        return False, f"Max open positions ({max_pos}) reached"
    return True, "OK"


# --- 6. Agent class ---

class RiskManager(Agent):
    def __init__(self, config: Optional[dict] = None):
        super().__init__("risk_manager", config or _load_config())

    def run(self, context: Optional[dict] = None) -> AgentResult:
        self._status = AgentStatus.RUNNING
        ctx = context or {}
        symbol = ctx.get("symbol", "RELIANCE")
        entry_price = ctx.get("entry_price", 0.0)
        win_rate = ctx.get("win_rate", 55.0)
        avg_win = ctx.get("avg_win", 2.5)
        avg_loss = ctx.get("avg_loss", 1.5)
        open_positions = ctx.get("open_positions", [])
        daily_pnl_pct = ctx.get("daily_pnl_pct", 0.0)
        weekly_pnl_pct = ctx.get("weekly_pnl_pct", 0.0)
        monthly_pnl_pct = ctx.get("monthly_pnl_pct", 0.0)

        capital = self.config.get("trading", {}).get("capital", 10000)
        kelly_frac = self.config.get("risk", {}).get("kelly_fraction", 0.5)

        # Loss limits
        allowed, reason = check_trade_allowed(daily_pnl_pct, weekly_pnl_pct, monthly_pnl_pct)
        if not allowed:
            return self._result({"allowed": False, "position_size": 0.0, "stop_loss": 0.0, "reason": reason})

        # Portfolio checks
        corr_ok, corr_reason = check_correlation(symbol, open_positions)
        if not corr_ok:
            return self._result({"allowed": False, "position_size": 0.0, "stop_loss": 0.0, "reason": corr_reason})

        sector_ok, sector_reason = check_sector_overlap(symbol, open_positions)
        if not sector_ok:
            return self._result({"allowed": False, "position_size": 0.0, "stop_loss": 0.0, "reason": sector_reason})

        # Position sizing
        size_multiplier = 0.5 if "reducing" in reason.lower() else 1.0
        position_size = kelly_size(win_rate, avg_win, avg_loss, capital, kelly_frac) * size_multiplier

        # Stop loss
        stop_loss = atr_stop_loss(entry_price, symbol) if entry_price > 0 else 0.0

        return self._result({
            "allowed": True,
            "position_size": round(position_size, 2),
            "stop_loss": round(stop_loss, 2),
            "reason": reason,
        })


# --- 7. Demo ---

if __name__ == "__main__":
    cfg = _load_config()
    rm = RiskManager(cfg)

    context = {
        "symbol": "RELIANCE",
        "entry_price": 1390.0,
        "win_rate": 58.0,
        "avg_win": 3.2,
        "avg_loss": 1.8,
        "open_positions": ["INFY"],
        "daily_pnl_pct": -1.0,
        "weekly_pnl_pct": -3.0,
        "monthly_pnl_pct": -5.0,
    }

    result = rm.run(context)
    print("=" * 50)
    print("RISK MANAGER – Position Sizing Demo")
    print("=" * 50)
    print(f"Symbol:         {context['symbol']}")
    print(f"Entry Price:    ₹{context['entry_price']}")
    print(f"Win Rate:       {context['win_rate']}%")
    print(f"Avg Win:        {context['avg_win']}%")
    print(f"Avg Loss:       {context['avg_loss']}%")
    print(f"Capital:        ₹{cfg['trading']['capital']}")
    print("-" * 50)
    print(f"Allowed:        {result.data['allowed']}")
    print(f"Position Size:  ₹{result.data['position_size']}")
    print(f"Stop Loss:      ₹{result.data['stop_loss']}")
    print(f"Reason:         {result.data['reason']}")
    print("-" * 50)

    # Trailing stop demo
    current_stop = result.data["stop_loss"]
    current_price = 1410.0
    new_stop = trailing_stop(current_price, context["entry_price"], current_stop)
    print(f"Trailing Stop:  ₹{new_stop:.2f} (price at ₹{current_price})")

    # Kelly sizing standalone
    ks = kelly_size(58.0, 3.2, 1.8, 10000)
    print(f"Kelly Size:     ₹{ks:.2f} (half-Kelly)")
    print("=" * 50)
