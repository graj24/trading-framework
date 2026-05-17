"""
PM Trader Daemon — per-PM standing process.

Listens for exec_order.<pm_id> events on the event bus.
Runs deterministic pre-trade gates (no LLM in this path), then places the order.

Run one instance per PM:
  python -m agents.pm_trader --pm_id 1
"""
from __future__ import annotations

import argparse
import logging
import time
from datetime import datetime

from core.event_bus import get_bus
from core.broker import get_broker, is_kill_switch_active
from core.pm_runtime import get_pm_config
from agents.risk_manager import check_circuit_breaker, audit_log

logger = logging.getLogger(__name__)


def _pre_trade_gates(pm_id: str, order: dict, config: dict) -> tuple[bool, str]:
    """Deterministic gates — no LLM. Returns (allowed, reason)."""
    # Kill switch
    if is_kill_switch_active():
        return False, "Kill switch active"

    # Per-PM pause
    pause_path = Path(f"pm_{pm_id}/state/PAUSED")
    if pause_path.exists():
        return False, f"PM{pm_id} is paused: {pause_path.read_text()}"

    # Circuit breaker
    allowed, reason = check_circuit_breaker(pm_id, config)
    if not allowed:
        return False, reason

    # Basic order sanity
    if not order.get("symbol"):
        return False, "Missing symbol"
    qty = order.get("qty", 0)
    if qty <= 0:
        return False, f"Invalid qty: {qty}"

    return True, "OK"


def _execute(pm_id: str, order: dict, config: dict):
    allowed, reason = _pre_trade_gates(pm_id, order, config)
    if not allowed:
        logger.warning(f"PM{pm_id} Trader: order blocked — {reason}")
        audit_log(pm_id, "ORDER_BLOCKED", {"reason": reason, "order": order})
        return

    # Halve size if weekly loss threshold hit
    _, cb_reason = check_circuit_breaker(pm_id, config)
    size_mult = 0.5 if "halved" in cb_reason.lower() else 1.0

    broker = get_broker(config)
    symbol = order["symbol"]
    qty = max(1, int(order.get("qty", 1) * size_mult))
    price = order.get("price", 0.0)
    sl = order.get("sl", 0.0)
    order_type = order.get("order_type", "MARKET")
    tag = order.get("tag", f"pm{pm_id}")

    try:
        order_id = broker.place_order(
            symbol=symbol, qty=qty, order_type=order_type,
            price=price, sl=sl, tag=tag, pm_id=pm_id,
        )
        audit_log(pm_id, "ORDER_PLACED", {
            "order_id": order_id, "symbol": symbol, "qty": qty,
            "price": price, "sl": sl, "tag": tag,
        })
        # Publish fill event
        get_bus().publish(
            f"fill.{pm_id}",
            {"order_id": order_id, "symbol": symbol, "qty": qty, "price": price},
            pm_id=pm_id,
            severity="INFO",
        )
        logger.info(f"PM{pm_id} Trader: placed {order_type} {qty}×{symbol} → {order_id}")
    except Exception as e:
        logger.error(f"PM{pm_id} Trader: order failed — {e}")
        audit_log(pm_id, "ORDER_FAILED", {"error": str(e), "order": order})


def run(pm_id: str):
    from pathlib import Path
    from core.config import get_config
    config = get_config()
    bus = get_bus()

    cursor_path = Path(f"pm_{pm_id}/state/trader_cursor.txt")
    cursor_path.parent.mkdir(parents=True, exist_ok=True)
    cursor = int(cursor_path.read_text().strip()) if cursor_path.exists() else bus.latest_id()

    fh = logging.FileHandler(f"logs/pm{pm_id}_trader.log")
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(fh)

    logger.info(f"PM{pm_id} Trader daemon started — cursor={cursor}")
    bus.publish(f"system.daemon.{pm_id}", {"daemon": "trader", "event": "start"}, pm_id=pm_id)

    topic = f"exec_order.{pm_id}"
    while True:
        try:
            events = bus.subscribe(topic, since_id=cursor)
            for event in events:
                cursor = event["id"]
                _execute(pm_id, event["payload"], config)
            cursor_path.write_text(str(cursor))
        except Exception as e:
            logger.error(f"PM{pm_id} Trader error: {e}")
        time.sleep(1)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("--pm_id", required=True)
    args = parser.parse_args()
    run(args.pm_id)
