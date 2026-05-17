"""
PM Risk Daemon — per-PM standing process.

Continuously monitors portfolio VaR and P&L.
On breach: publishes risk.breach event, updates positions snapshot, escalates to PM wakeup.

Run one instance per PM:
  python -m agents.pm_risk --pm_id 1
"""
from __future__ import annotations

import argparse
import logging
import sqlite3
import time
from datetime import date, datetime
from pathlib import Path

from core.event_bus import get_bus
from core.pm_state import refresh_positions, read_positions
from core.pm_runtime import get_pm_config
from agents.risk_manager import check_circuit_breaker, audit_log

logger = logging.getLogger(__name__)

POLL_INTERVAL = 30  # seconds between checks


def _compute_var(positions: list[dict], confidence: float = 0.95) -> float:
    """
    Simple historical VaR: sum of (entry_price * qty * 2% move) as a proxy.
    Replace with proper historical simulation when price history is available.
    """
    if not positions:
        return 0.0
    total_exposure = sum(
        abs(p.get("entry_price", 0) * p.get("quantity", p.get("qty", 0)))
        for p in positions
    )
    # 2% daily move at 95% confidence (rough proxy)
    return total_exposure * 0.02


def _check_var_breach(pm_id: str, positions: list[dict], config: dict) -> bool:
    var = _compute_var(positions)
    capital = config.get("trading", {}).get("capital", 10000)
    var_pct = var / capital * 100 if capital else 0
    max_var_pct = config.get("risk", {}).get("max_var_pct", 5.0)

    if var_pct > max_var_pct:
        reason = f"VaR {var_pct:.1f}% exceeds limit {max_var_pct}%"
        logger.warning(f"PM{pm_id} Risk: {reason}")
        audit_log(pm_id, "VAR_BREACH", {"var_pct": var_pct, "limit": max_var_pct, "var_inr": var})
        get_bus().publish(
            f"risk.breach.{pm_id}",
            {"reason": reason, "var_pct": var_pct, "var_inr": var},
            pm_id=pm_id,
            severity="HIGH",
        )
        # Escalate to PM wakeup
        get_bus().publish(
            f"pm.wakeup.{pm_id}",
            {"trigger": "var_breach", "reason": reason},
            pm_id=pm_id,
            severity="HIGH",
        )
        return True
    return False


def run(pm_id: str):
    from core.config import get_config
    config = get_config()

    fh = logging.FileHandler(f"logs/pm{pm_id}_risk.log")
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(fh)

    logger.info(f"PM{pm_id} Risk daemon started (poll={POLL_INTERVAL}s)")
    get_bus().publish(f"system.daemon.{pm_id}", {"daemon": "risk", "event": "start"}, pm_id=pm_id)

    while True:
        try:
            # Refresh positions snapshot from DB
            refresh_positions(pm_id)
            positions = read_positions(pm_id)

            # Circuit breaker check
            allowed, reason = check_circuit_breaker(pm_id, config)
            if not allowed:
                logger.warning(f"PM{pm_id} Risk: circuit breaker — {reason}")

            # VaR check
            _check_var_breach(pm_id, positions, config)

        except Exception as e:
            logger.error(f"PM{pm_id} Risk error: {e}")

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("--pm_id", required=True)
    args = parser.parse_args()
    run(args.pm_id)
