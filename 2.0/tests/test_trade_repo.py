"""Integration tests for ``trade_repo`` against a real Postgres.

K3 Step 3.4 verification. Spins up Postgres via testcontainers, runs
``alembic upgrade head`` (so 0001/0002/0003 are exercised), and uses the
asyncpg pool the production control plane uses. Marked ``integration``
— not part of ``make test``; run via ``make test-all`` or
``pytest -m integration``.

Each test starts with empty ``paper_trades`` (CASCADE-truncated alongside
``pms``). One PM is seeded in setup so the FK from ``paper_trades.pm_id``
holds.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import asyncpg
import pytest

from agora.platform.control_plane import pm_repo, trade_repo

testcontainers = pytest.importorskip("testcontainers.postgres")
PostgresContainer = testcontainers.PostgresContainer

REPO_2_0 = Path(__file__).resolve().parent.parent

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def pg_url() -> Any:
    """One-shot Postgres + alembic upgrade. Yields an asyncpg-flavoured URL."""
    with PostgresContainer("postgres:16-alpine", username="agora", password="agora") as pg:
        url = pg.get_connection_url()
        if "+psycopg2" in url:
            url = url.replace("+psycopg2", "")
        proc = subprocess.run(
            ["uv", "run", "alembic", "upgrade", "head"],
            cwd=REPO_2_0,
            env={"POSTGRES_URL": url, "PATH": os.environ["PATH"]},
            capture_output=True,
            text=True,
            check=False,
        )
        assert proc.returncode == 0, proc.stderr
        yield url


@pytest.fixture
async def pool(pg_url: str) -> AsyncIterator[asyncpg.Pool]:
    p = await asyncpg.create_pool(pg_url, min_size=1, max_size=4)
    assert p is not None
    try:
        yield p
    finally:
        await p.close()


@pytest.fixture(autouse=True)
async def _truncate_and_seed(pool: asyncpg.Pool) -> AsyncIterator[None]:
    """Reset state and seed one PM (FK source for paper_trades.pm_id)."""
    async with pool.acquire() as conn:
        await conn.execute("TRUNCATE TABLE paper_trades RESTART IDENTITY CASCADE")
        await conn.execute("TRUNCATE TABLE pms CASCADE")
    await pm_repo.insert_pm(
        pool,
        pm_id="pm1",
        name="PM1",
        starting_capital_inr=1_000_000.0,
        prompt_path="/dev/null",
        config={},
    )
    yield


def _now() -> datetime:
    return datetime.now(UTC)


async def test_insert_open_trade_returns_id(pool: asyncpg.Pool) -> None:
    trade_id = await trade_repo.insert_open_trade(
        pool,
        pm_id="pm1",
        symbol="RELIANCE",
        side="LONG",
        quantity=10,
        entry_price=Decimal("1500.00"),
        entry_ts=_now(),
        stop_loss=Decimal("1450.00"),
        target=Decimal("1600.00"),
        strategy_id="momentum_v1",
        metadata={"signal": "sma_crossup"},
    )
    assert isinstance(trade_id, int)
    assert trade_id > 0

    record = await trade_repo.get_trade(pool, trade_id)
    assert record is not None
    assert record.pm_id == "pm1"
    assert record.symbol == "RELIANCE"
    assert record.side == "LONG"
    assert record.quantity == 10
    assert record.outcome == "open"
    assert record.entry_price == Decimal("1500.00")
    assert record.exit_price is None
    assert record.metadata == {"signal": "sma_crossup"}


async def test_close_trade_computes_pnl_long(pool: asyncpg.Pool) -> None:
    trade_id = await trade_repo.insert_open_trade(
        pool,
        pm_id="pm1",
        symbol="RELIANCE",
        side="LONG",
        quantity=10,
        entry_price=Decimal("1500.00"),
        entry_ts=_now(),
    )
    closed = await trade_repo.close_trade(
        pool,
        trade_id,
        exit_price=Decimal("1600.00"),
        exit_ts=_now(),
        outcome="target_hit",
    )
    # PnL: (1600 - 1500) * 10 = 1000 INR; pct = 1000 / 15000 * 100 = 6.6...%
    assert closed.outcome == "target_hit"
    assert closed.pnl_inr == Decimal("1000.00")
    assert closed.pnl_pct is not None
    assert closed.pnl_pct.quantize(Decimal("0.01")) == Decimal("6.67")
    assert closed.exit_price == Decimal("1600.00")
    assert closed.exit_ts is not None


async def test_close_trade_returns_record_with_outcome(pool: asyncpg.Pool) -> None:
    """Outcome and PnL must round-trip through the close path verbatim."""
    trade_id = await trade_repo.insert_open_trade(
        pool,
        pm_id="pm1",
        symbol="TCS",
        side="LONG",
        quantity=5,
        entry_price=Decimal("3000.00"),
        entry_ts=_now(),
        stop_loss=Decimal("2900.00"),
    )
    closed = await trade_repo.close_trade(
        pool,
        trade_id,
        exit_price=Decimal("2900.00"),
        exit_ts=_now(),
        outcome="sl_hit",
    )
    # Loss: (2900 - 3000) * 5 = -500 INR.
    assert closed.outcome == "sl_hit"
    assert closed.pnl_inr == Decimal("-500.00")
    assert closed.id == trade_id

    # Reading back through get_trade returns the same record.
    fetched = await trade_repo.get_trade(pool, trade_id)
    assert fetched is not None
    assert fetched.outcome == "sl_hit"
    assert fetched.pnl_inr == Decimal("-500.00")


async def test_close_trade_rejects_double_close(pool: asyncpg.Pool) -> None:
    trade_id = await trade_repo.insert_open_trade(
        pool,
        pm_id="pm1",
        symbol="INFY",
        side="LONG",
        quantity=2,
        entry_price=Decimal("1700.00"),
        entry_ts=_now(),
    )
    await trade_repo.close_trade(
        pool,
        trade_id,
        exit_price=Decimal("1750.00"),
        exit_ts=_now(),
        outcome="manual",
    )
    with pytest.raises(ValueError, match="already closed"):
        await trade_repo.close_trade(
            pool,
            trade_id,
            exit_price=Decimal("1700.00"),
            exit_ts=_now(),
            outcome="eod_close",
        )


async def test_close_trade_rejects_open_outcome(pool: asyncpg.Pool) -> None:
    trade_id = await trade_repo.insert_open_trade(
        pool,
        pm_id="pm1",
        symbol="SBIN",
        side="LONG",
        quantity=1,
        entry_price=Decimal("800.00"),
        entry_ts=_now(),
    )
    with pytest.raises(ValueError, match="terminal outcome"):
        await trade_repo.close_trade(
            pool,
            trade_id,
            exit_price=Decimal("810.00"),
            exit_ts=_now(),
            outcome="open",
        )


async def test_list_open_trades_filters_correctly(pool: asyncpg.Pool) -> None:
    # Insert 3 open trades, close 1, list_open should return 2.
    open_a = await trade_repo.insert_open_trade(
        pool,
        pm_id="pm1",
        symbol="RELIANCE",
        side="LONG",
        quantity=10,
        entry_price=Decimal("1500"),
        entry_ts=_now(),
    )
    open_b = await trade_repo.insert_open_trade(
        pool,
        pm_id="pm1",
        symbol="TCS",
        side="LONG",
        quantity=5,
        entry_price=Decimal("3000"),
        entry_ts=_now(),
    )
    closed_c = await trade_repo.insert_open_trade(
        pool,
        pm_id="pm1",
        symbol="INFY",
        side="LONG",
        quantity=2,
        entry_price=Decimal("1700"),
        entry_ts=_now(),
    )
    await trade_repo.close_trade(
        pool,
        closed_c,
        exit_price=Decimal("1750"),
        exit_ts=_now(),
        outcome="target_hit",
    )

    open_trades = await trade_repo.list_open_trades(pool, "pm1")
    open_ids = {t.id for t in open_trades}
    assert open_ids == {open_a, open_b}
    assert all(t.outcome == "open" for t in open_trades)

    all_trades = await trade_repo.list_trades(pool, "pm1")
    assert len(all_trades) == 3


async def test_get_trade_returns_none_when_missing(pool: asyncpg.Pool) -> None:
    assert await trade_repo.get_trade(pool, 999_999) is None
