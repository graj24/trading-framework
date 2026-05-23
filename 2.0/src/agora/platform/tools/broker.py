"""Broker tool — AGORA-side gateway for placing paper orders.

K3 Step 3.4. Validates the kill switch and the PM's lifecycle status,
then "submits" an order through the AGORA-shaped path. K3 simulates an
immediate fill at the requested ``entry_price`` and writes a
``paper_trades`` row with ``outcome='open'``. K3.5+ wires the real
NautilusTrader execution path through this tool; K8 swaps in a real
Zerodha adapter behind the same ``submit_order`` shape.

The tool is the AGORA-side gateway, not the NautilusTrader engine
itself. The engine runs inside a backtest activity (K3.5+); the broker
tool is what the trading-cycle activity calls to "place an order" — it
does the safety checks, talks to whichever engine is registered, and
records the trade.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import asyncpg

from agora.platform.control_plane.trade_repo import (
    TradeSide,
    insert_open_trade,
)


class BrokerError(Exception):
    """Order rejected by the AGORA broker.

    Reasons: kill switch active, PM not in ``running`` state, or an
    underlying engine failure once K3.5 wires real execution.
    """


@dataclass(frozen=True)
class OrderRequest:
    """Caller-provided order spec. Pure data — no DB or engine state."""

    pm_id: str
    symbol: str
    side: TradeSide
    quantity: int
    entry_price: Decimal
    stop_loss: Decimal | None = None
    target: Decimal | None = None
    strategy_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class OrderResult:
    """Return shape from a successful :func:`submit_order` call."""

    trade_id: int
    pm_id: str
    symbol: str
    side: TradeSide
    quantity: int
    entry_price: Decimal
    entry_ts: datetime

    def summary(self) -> str:
        return (
            f"{self.side} {self.quantity} {self.symbol} @ ₹{self.entry_price} "
            f"[trade_id={self.trade_id}]"
        )


# Type aliases for the injectable safety checks. Defaults below talk to
# Postgres directly; tests override them with awaitable lambdas.
KillSwitchCheck = Callable[[asyncpg.Pool], Awaitable[bool]]
PMStatusCheck = Callable[[asyncpg.Pool, str], Awaitable[str]]


async def is_kill_switch_active(pool: asyncpg.Pool) -> bool:
    """Read the kill_switch.active flag.

    Cached for 1s in-process to avoid hot-loop hammering — see plan
    §5 Step 3.7. K3.4 ships the bare uncached read; the cache lives
    in the K3.7 endpoint module (or here once 3.7 lands). Keeping it
    simple here.
    """
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT active FROM kill_switch WHERE id = 1")
    return bool(row and row["active"])


async def check_pm_status(pool: asyncpg.Pool, pm_id: str) -> str:
    """Read pms.status. Returns the current status string, or ``'missing'``
    if the PM does not exist. K3 callers treat anything other than
    ``'running'`` as a rejection.
    """
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT status FROM pms WHERE id = $1", pm_id)
    return str(row["status"]) if row else "missing"


async def submit_order(
    pool: asyncpg.Pool,
    order: OrderRequest,
    *,
    kill_switch_check: KillSwitchCheck | None = None,
    pm_status_check: PMStatusCheck | None = None,
) -> OrderResult:
    """Submit ``order`` to the AGORA broker.

    Order of checks (matters for security and journal clarity):

    1. **Kill switch** — if active, raise ``BrokerError("kill switch active")``.
       This wins over any PM-state check; the kill switch is the global
       cut-off and must be visible in the rejection log even if the PM
       was also in a bad state.
    2. **PM status** — if not ``'running'``, raise ``BrokerError`` with
       the actual status. Missing PM IDs surface as ``'missing'``.
    3. **(Future K3.5+)** NautilusTrader engine submit. K3.4 simulates
       an immediate fill at ``order.entry_price``.
    4. **Insert** into ``paper_trades`` with ``outcome='open'``.

    Returns an :class:`OrderResult` with the new ``trade_id``.

    The ``kill_switch_check`` and ``pm_status_check`` parameters are
    injection points for tests — production callers leave them ``None``
    so the defaults (asyncpg reads) are used.
    """
    ks_check = kill_switch_check or is_kill_switch_active
    status_check = pm_status_check or check_pm_status

    if await ks_check(pool):
        raise BrokerError("kill switch active")
    pm_status = await status_check(pool, order.pm_id)
    if pm_status != "running":
        raise BrokerError(f"pm {order.pm_id!r} is {pm_status}, not running")

    # K3.4: simulate immediate fill at entry_price. K3.5+ wires real
    # NautilusTrader execution.
    entry_ts = datetime.now(UTC)
    trade_id = await insert_open_trade(
        pool,
        pm_id=order.pm_id,
        symbol=order.symbol,
        side=order.side,
        quantity=order.quantity,
        entry_price=order.entry_price,
        entry_ts=entry_ts,
        stop_loss=order.stop_loss,
        target=order.target,
        strategy_id=order.strategy_id,
        metadata=order.metadata or {},
    )
    return OrderResult(
        trade_id=trade_id,
        pm_id=order.pm_id,
        symbol=order.symbol,
        side=order.side,
        quantity=order.quantity,
        entry_price=order.entry_price,
        entry_ts=entry_ts,
    )


__all__ = [
    "BrokerError",
    "KillSwitchCheck",
    "OrderRequest",
    "OrderResult",
    "PMStatusCheck",
    "check_pm_status",
    "is_kill_switch_active",
    "submit_order",
]
