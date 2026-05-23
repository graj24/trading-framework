"""Integration test for ``record_budget_event``.

Gated behind the ``integration`` marker because it spins up a Postgres
container via testcontainers and runs alembic migrations against it.
Not part of the default ``make test`` run (run with ``-m integration``).

Verifies:
  * the recorder returns the new row id;
  * a follow-up SELECT shows the row;
  * passing ``pm_id=None`` returns None and writes nothing.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from agora.platform.llm.budget import record_budget_event
from agora.platform.shared.settings import Settings

testcontainers = pytest.importorskip("testcontainers.postgres")
PostgresContainer = testcontainers.PostgresContainer

REPO_2_0 = Path(__file__).resolve().parent.parent

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def pg_url() -> Any:
    """One-shot Postgres + alembic upgrade. Yields the asyncpg-flavoured URL."""
    with PostgresContainer("postgres:16-alpine", username="agora", password="agora") as pg:
        url = pg.get_connection_url()
        if "+psycopg2" in url:
            url = url.replace("+psycopg2", "")
        # alembic env.py rewrites both psycopg2 and asyncpg to psycopg3 for
        # the migration run, but the app talks asyncpg.
        proc = subprocess.run(
            ["uv", "run", "alembic", "upgrade", "head"],
            cwd=REPO_2_0,
            env={"POSTGRES_URL": url, "PATH": os.environ["PATH"]},
            capture_output=True,
            text=True,
            check=False,
        )
        assert proc.returncode == 0, proc.stderr
        async_url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
        yield async_url


@pytest.fixture
async def settings(pg_url: str) -> Settings:
    return Settings(_env_file=None, postgres_url=pg_url)


@pytest.fixture
async def smoke_pm(settings: Settings) -> AsyncIterator[str]:
    """Insert a placeholder PM row so the FK on budget_events resolves."""
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(settings.postgres_url, future=True)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as session:
        await session.execute(
            text(
                """
                INSERT INTO pms (id, name, status, starting_capital_inr, prompt_path)
                VALUES (:id, :name, :status, :cap, :pp)
                ON CONFLICT (id) DO NOTHING
                """
            ),
            {"id": "smoke", "name": "smoke", "status": "stopped", "cap": 0, "pp": "/dev/null"},
        )
        await session.commit()
    try:
        yield "smoke"
    finally:
        await engine.dispose()


async def test_record_budget_event_writes_row(settings: Settings, smoke_pm: str) -> None:
    row_id = await record_budget_event(
        pm_id=smoke_pm,
        kind="llm_call",
        amount_usd=0.0042,
        metadata={"agent_id": "smoke", "model": "anthropic/claude-sonnet-4-5"},
        settings=settings,
    )
    assert isinstance(row_id, int)
    assert row_id > 0

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(settings.postgres_url, future=True)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sm() as session:
            row = (
                await session.execute(
                    text(
                        "SELECT pm_id, kind, amount_usd, metadata FROM budget_events "
                        "WHERE id = :id"
                    ),
                    {"id": row_id},
                )
            ).one()
        assert row.pm_id == "smoke"
        assert row.kind == "llm_call"
        assert float(row.amount_usd) == pytest.approx(0.0042)
        assert row.metadata["agent_id"] == "smoke"
    finally:
        await engine.dispose()


async def test_record_budget_event_skips_when_pm_id_none(settings: Settings) -> None:
    row_id = await record_budget_event(
        pm_id=None,
        kind="llm_call",
        amount_usd=1.0,
        metadata={},
        settings=settings,
    )
    assert row_id is None
