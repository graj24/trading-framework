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

import asyncio
import time
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


# ---- Kill-switch cache ----------------------------------------------------
# Plan §5 Step 3.7: ``is_kill_switch_active`` is hot-pathed by every order
# submit and (post-K3.5) every trading-cycle iteration. The 1-second
# in-process TTL caps DB chatter at one read per second per process.
# Cross-process invalidation is impossible without pub/sub; the API
# process invalidates synchronously after writes (see endpoints in
# ``app.py``); the worker process picks up changes within the TTL.
_KILL_SWITCH_CACHE_TTL_S: float = 1.0


@dataclass
class _KillSwitchCacheEntry:
    """Cached value + the monotonic timestamp it was read at."""

    value: bool
    fetched_at: float


_kill_switch_cache: _KillSwitchCacheEntry | None = None
_kill_switch_cache_lock = asyncio.Lock()


async def is_kill_switch_active(pool: asyncpg.Pool) -> bool:
    """Read the kill_switch.active flag, with a 1s in-process cache.

    Plan §5 Step 3.7: cached for 1s in-process to avoid hot-loop
    hammering when a trading cycle is firing every second. The cache
    is process-local and there is no cross-process invalidation; the
    1s TTL is the bound. The API process invalidates its own cache
    via :func:`_invalidate_kill_switch_cache` from the toggle
    endpoints so a flip is reflected immediately for any code path
    in that process; the worker process picks the change up within
    one TTL.
    """
    global _kill_switch_cache
    now = time.monotonic()
    cached = _kill_switch_cache
    if cached is not None and (now - cached.fetched_at) < _KILL_SWITCH_CACHE_TTL_S:
        return cached.value
    async with _kill_switch_cache_lock:
        # Double-check inside the lock — another waiter may have
        # populated the cache while we were queued.
        cached = _kill_switch_cache
        if cached is not None and (time.monotonic() - cached.fetched_at) < _KILL_SWITCH_CACHE_TTL_S:
            return cached.value
        async with pool.acquire() as conn:
            row = await conn.fetchrow("SELECT active FROM kill_switch WHERE id = 1")
        value = bool(row and row["active"])
        _kill_switch_cache = _KillSwitchCacheEntry(value=value, fetched_at=time.monotonic())
        return value


def _invalidate_kill_switch_cache() -> None:
    """Drop the cached kill-switch value.

    Called from the activate/deactivate endpoints after the DB write
    succeeds so the API process picks up the new state immediately.
    Tests use this to reset cache between cases.
    """
    global _kill_switch_cache
    _kill_switch_cache = None


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
    "_invalidate_kill_switch_cache",
    "check_pm_status",
    "is_kill_switch_active",
    "submit_order",
]
