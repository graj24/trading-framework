"""Integration tests for ``pm_repo`` against a real Postgres.

Spins up Postgres via testcontainers, runs alembic upgrade head (so both
0001 and 0002 are exercised), and uses the asyncpg pool the production
control plane uses. Marked ``integration`` — not part of ``make test``.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import asyncpg
import pytest

from agora.platform.control_plane import pm_repo

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
        # Asyncpg expects a plain postgresql:// URL (no SQLAlchemy driver tag).
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
async def _truncate_pms(pool: asyncpg.Pool) -> AsyncIterator[None]:
    """Each test starts with an empty ``pms`` table.

    CASCADE because the FK from agents/runs/budget_events references
    ``pms.id``; we don't insert into those in this suite but the cascade
    keeps the cleanup robust against future test additions.
    """
    async with pool.acquire() as conn:
        await conn.execute("TRUNCATE TABLE pms CASCADE")
    yield


async def test_insert_get_roundtrip(pool: asyncpg.Pool) -> None:
    await pm_repo.insert_pm(
        pool,
        pm_id="pm1",
        name="PM1",
        starting_capital_inr=1_000_000.0,
        prompt_path="/dev/null",
        config={"foo": "bar"},
    )
    record = await pm_repo.get_pm(pool, "pm1")
    assert record is not None
    assert record.id == "pm1"
    assert record.name == "PM1"
    assert record.status == "provisioning"
    assert record.starting_capital_inr == pytest.approx(1_000_000.0)
    assert record.prompt_path == "/dev/null"
    assert record.config == {"foo": "bar"}
    assert record.workflow_id is None
    assert record.stopped_at is None


async def test_get_pm_returns_none_when_missing(pool: asyncpg.Pool) -> None:
    assert await pm_repo.get_pm(pool, "missing") is None


async def test_pm_exists(pool: asyncpg.Pool) -> None:
    assert await pm_repo.pm_exists(pool, "pm1") is False
    await pm_repo.insert_pm(
        pool,
        pm_id="pm1",
        name="PM1",
        starting_capital_inr=1.0,
        prompt_path="/dev/null",
        config={},
    )
    assert await pm_repo.pm_exists(pool, "pm1") is True


async def test_list_pms_empty_then_one(pool: asyncpg.Pool) -> None:
    assert await pm_repo.list_pms(pool) == []
    await pm_repo.insert_pm(
        pool,
        pm_id="pm1",
        name="PM1",
        starting_capital_inr=1.0,
        prompt_path="/dev/null",
        config={},
    )
    summaries = await pm_repo.list_pms(pool)
    assert len(summaries) == 1
    assert summaries[0].id == "pm1"
    assert summaries[0].status == "provisioning"


async def test_update_status(pool: asyncpg.Pool) -> None:
    await pm_repo.insert_pm(
        pool,
        pm_id="pm1",
        name="PM1",
        starting_capital_inr=1.0,
        prompt_path="/dev/null",
        config={},
    )
    await pm_repo.update_pm_status(pool, "pm1", "running")
    record = await pm_repo.get_pm(pool, "pm1")
    assert record is not None
    assert record.status == "running"


async def test_update_workflow_id(pool: asyncpg.Pool) -> None:
    await pm_repo.insert_pm(
        pool,
        pm_id="pm1",
        name="PM1",
        starting_capital_inr=1.0,
        prompt_path="/dev/null",
        config={},
    )
    await pm_repo.update_pm_workflow_id(pool, "pm1", "pm-pm1")
    record = await pm_repo.get_pm(pool, "pm1")
    assert record is not None
    assert record.workflow_id == "pm-pm1"


async def test_unique_pm_id(pool: asyncpg.Pool) -> None:
    await pm_repo.insert_pm(
        pool,
        pm_id="pm1",
        name="PM1",
        starting_capital_inr=1.0,
        prompt_path="/dev/null",
        config={},
    )
    with pytest.raises(asyncpg.UniqueViolationError):
        await pm_repo.insert_pm(
            pool,
            pm_id="pm1",
            name="PM1-dup",
            starting_capital_inr=2.0,
            prompt_path="/dev/null",
            config={},
        )


async def test_concurrent_inserts_one_succeeds(pool: asyncpg.Pool) -> None:
    """Two concurrent inserts of the same id: exactly one wins."""

    async def insert() -> bool:
        try:
            await pm_repo.insert_pm(
                pool,
                pm_id="race",
                name="race",
                starting_capital_inr=1.0,
                prompt_path="/dev/null",
                config={},
            )
            return True
        except asyncpg.UniqueViolationError:
            return False

    results = await asyncio.gather(insert(), insert(), return_exceptions=False)
    assert sum(1 for r in results if r) == 1
