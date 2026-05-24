"""Tests for the 1-second kill-switch in-process cache.

K3 Step 3.7. ``broker.is_kill_switch_active`` caches the
``kill_switch.active`` flag for 1 second per process to avoid hot-loop
DB hammering. The cache is invalidated synchronously by the toggle
endpoints (in the API process); cross-process invalidation is bounded
by the TTL.

Coverage:

* Two consecutive calls within 1s hit Postgres exactly once.
* ``_invalidate_kill_switch_cache`` forces the next call to refetch.
* Sleeping past the TTL also forces a refetch.
* Concurrent first callers race on the lock — only one fetch happens.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest

from agora.platform.tools import broker as broker_module

# ----- Fake pool -----------------------------------------------------------


class _FakeConnection:
    def __init__(self, table: dict[str, bool], counter: list[int]) -> None:
        self._table = table
        self._counter = counter

    async def fetchrow(self, sql: str, *args: Any) -> dict[str, bool] | None:
        self._counter[0] += 1
        return {"active": self._table["active"]}


class _FakeAcquireCM:
    def __init__(self, conn: _FakeConnection) -> None:
        self._conn = conn

    async def __aenter__(self) -> _FakeConnection:
        return self._conn

    async def __aexit__(self, *_: Any) -> None:
        return None


class _FakePool:
    def __init__(self) -> None:
        self.table: dict[str, bool] = {"active": False}
        self.fetchrow_calls: list[int] = [0]

    def acquire(self) -> _FakeAcquireCM:
        return _FakeAcquireCM(_FakeConnection(self.table, self.fetchrow_calls))


# ----- Fixtures ------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    """Each test starts with a clean cache."""
    broker_module._invalidate_kill_switch_cache()


@pytest.fixture
def pool() -> _FakePool:
    return _FakePool()


# ----- Tests ---------------------------------------------------------------


async def test_two_calls_within_ttl_hit_pool_once(pool: _FakePool) -> None:
    """First call hits the pool; second within 1s reads the cache."""
    v1 = await broker_module.is_kill_switch_active(pool)
    v2 = await broker_module.is_kill_switch_active(pool)
    assert v1 is False
    assert v2 is False
    assert pool.fetchrow_calls[0] == 1


async def test_invalidate_forces_refetch(pool: _FakePool) -> None:
    """After explicit invalidation, the next call hits the pool again."""
    await broker_module.is_kill_switch_active(pool)
    assert pool.fetchrow_calls[0] == 1
    # Flip the underlying value and invalidate; the next call must see
    # the new value.
    pool.table["active"] = True
    broker_module._invalidate_kill_switch_cache()
    v = await broker_module.is_kill_switch_active(pool)
    assert v is True
    assert pool.fetchrow_calls[0] == 2


async def test_ttl_expiry_forces_refetch(pool: _FakePool) -> None:
    """After the TTL elapses (simulated via monotonic offset), refetch."""
    await broker_module.is_kill_switch_active(pool)
    assert pool.fetchrow_calls[0] == 1
    # Push the cache entry's fetched_at into the past so the next call
    # sees it as stale. We do NOT actually sleep — that would slow
    # ``make ci-local`` down.
    cache = broker_module._kill_switch_cache
    assert cache is not None
    broker_module._kill_switch_cache = broker_module._KillSwitchCacheEntry(
        value=cache.value,
        fetched_at=cache.fetched_at - 2.0,  # well past 1s TTL
    )
    pool.table["active"] = True
    v = await broker_module.is_kill_switch_active(pool)
    assert v is True
    assert pool.fetchrow_calls[0] == 2


async def test_concurrent_first_callers_race_on_lock(pool: _FakePool) -> None:
    """Three concurrent first-callers must produce exactly one fetch.

    The lock + double-check pattern in ``is_kill_switch_active``
    prevents N tasks from each issuing their own SELECT on cold-cache.
    """
    pool.table["active"] = True
    results = await asyncio.gather(
        broker_module.is_kill_switch_active(pool),
        broker_module.is_kill_switch_active(pool),
        broker_module.is_kill_switch_active(pool),
    )
    assert tuple(results) == (True, True, True)
    assert pool.fetchrow_calls[0] == 1


async def test_real_sleep_invalidates_cache(pool: _FakePool) -> None:
    """End-to-end check: a real >1s sleep flips the staleness condition.

    Slower than the synthetic-offset test above (1.05s) but proves
    the wall-clock semantics. Marked as ``slow`` would over-engineer
    a 1s assertion; leave it in the default suite as a behaviour
    pin.
    """
    await broker_module.is_kill_switch_active(pool)
    assert pool.fetchrow_calls[0] == 1
    # Real sleep just past the TTL.
    await asyncio.sleep(1.05)
    pool.table["active"] = True
    v = await broker_module.is_kill_switch_active(pool)
    assert v is True
    assert pool.fetchrow_calls[0] == 2


def test_cache_constants_match_plan() -> None:
    """Plan §5 Step 3.7 mandates 1-second TTL.

    Pinning the constant prevents accidental drift via a refactor; if
    a future change wants a different cadence it should also update
    the plan and the dashboard polling assumptions.
    """
    assert broker_module._KILL_SWITCH_CACHE_TTL_S == 1.0


# ----- Regression: broker still consults the injected check ----------------


async def test_submit_order_uses_injected_kill_switch_check_not_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression for the K3.7 change: ``submit_order`` defaults to
    ``is_kill_switch_active`` (now cached) but still honours an
    injected ``kill_switch_check``. Tests in ``test_broker.py`` rely
    on this behaviour; this test pins it explicitly so the cache
    refactor doesn't silently break it.
    """
    from decimal import Decimal

    from agora.platform.tools.broker import OrderRequest, submit_order

    calls: list[bool] = []

    async def kill_check(_pool: Any) -> bool:
        calls.append(True)
        return False

    async def pm_running(_pool: Any, _pm_id: str) -> str:
        return "running"

    async def fake_insert(_pool: Any, **_: Any) -> int:
        return 1

    monkeypatch.setattr(broker_module, "insert_open_trade", fake_insert)

    await submit_order(
        pool=None,
        order=OrderRequest(
            pm_id="pm1",
            symbol="RELIANCE",
            side="LONG",
            quantity=10,
            entry_price=Decimal("1500"),
        ),
        kill_switch_check=kill_check,
        pm_status_check=pm_running,
    )

    # The injected check fired; the cached default did NOT (would have
    # been one fetchrow on the bare default path).
    assert calls == [True]
    # Cache should also be untouched.
    assert broker_module._kill_switch_cache is None


# Keep ``time`` import live for static analysis, even if not used yet.
_ = time
