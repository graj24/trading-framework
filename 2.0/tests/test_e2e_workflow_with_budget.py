"""End-to-end integration test for K1 (Step 1.9).

Wires together the three K1 surfaces:
  - Temporal: a Workflow + Activity that the worker would actually run.
  - AgoraLLM: invoked inside the activity with a stubbed completion_fn so
    we don't need real LLM credentials.
  - Postgres + budget_events: a real Postgres testcontainer with the
    full migration set applied — the activity records a real row, and
    the test re-reads it to confirm.

Gated behind ``integration`` so default ``make test`` stays fast. Run with
``pytest -m integration tests/test_e2e_workflow_with_budget.py``.

This test is the K1 "smoke that proves the platform talks to itself" —
the closest thing to a runnable demonstration that the lifecycle, the
LLM wrapper, and the cost ledger are wired through one another. Real
agents arrive in K2+; for K1 the assertion is just "the plumbing
holds water."

The workflow + activity definitions live in ``_e2e_workflow_module`` so
the Temporal sandbox's workflow-validation re-import does not see this
file's heavy imports (sqlalchemy, testcontainers, etc.).
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from agora.platform.shared.settings import Settings
from tests._e2e_workflow_module import HelloWithBudgetWorkflow, record_llm_call

testcontainers = pytest.importorskip("testcontainers.postgres")
PostgresContainer = testcontainers.PostgresContainer

REPO_2_0 = Path(__file__).resolve().parent.parent

pytestmark = pytest.mark.integration


# ------------------------------------------------------------------- fixtures


@pytest.fixture(scope="module")
def pg_url() -> Any:
    """Postgres testcontainer + alembic upgrade. Yields the asyncpg URL."""
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
        async_url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
        yield async_url


@pytest.fixture
async def smoke_pm(pg_url: str, monkeypatch: pytest.MonkeyPatch) -> str:
    """Insert a placeholder pms row and point AgoraLLM at the testcontainer."""
    monkeypatch.setenv("POSTGRES_URL", pg_url)
    # Reset the budget module's engine cache so the new POSTGRES_URL is used.
    from agora.platform.llm import budget as budget_mod

    budget_mod._engine_cache.clear()
    budget_mod._sessionmaker_cache.clear()

    engine = create_async_engine(pg_url, future=True)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sm() as session:
            await session.execute(
                text(
                    """
                    INSERT INTO pms (id, name, status, starting_capital_inr, prompt_path)
                    VALUES (:id, :name, :status, :cap, :pp)
                    ON CONFLICT (id) DO NOTHING
                    """
                ),
                {
                    "id": "e2e",
                    "name": "e2e",
                    "status": "stopped",
                    "cap": 0,
                    "pp": "/dev/null",
                },
            )
            await session.commit()
    finally:
        await engine.dispose()
    return "e2e"


async def _start_temporal_env() -> WorkflowEnvironment:
    """Best-effort env start, preferring time-skipping."""
    try:
        return await WorkflowEnvironment.start_time_skipping()
    except Exception:
        return await WorkflowEnvironment.start_local()


# ---------------------------------------------------------------------- test


async def test_workflow_runs_llm_call_and_records_budget(pg_url: str, smoke_pm: str) -> None:
    """One workflow → one activity → one AgoraLLM call → one budget row."""
    try:
        env = await _start_temporal_env()
    except Exception as e:
        pytest.skip(f"Temporal test server unavailable: {e}")

    async with (
        env,
        Worker(
            env.client,
            task_queue="test-e2e",
            workflows=[HelloWithBudgetWorkflow],
            activities=[record_llm_call],
        ),
    ):
        result = await env.client.execute_workflow(
            HelloWithBudgetWorkflow.run,
            smoke_pm,
            id="test-e2e-1",
            task_queue="test-e2e",
        )
        assert result == "hello, integration"

    settings = Settings(_env_file=None, postgres_url=pg_url)
    engine = create_async_engine(settings.postgres_url, future=True)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sm() as session:
            row = (
                await session.execute(
                    text(
                        """
                        SELECT pm_id, kind, amount_usd, metadata
                        FROM budget_events
                        WHERE pm_id = :pm_id
                        ORDER BY id DESC
                        LIMIT 1
                        """
                    ),
                    {"pm_id": smoke_pm},
                )
            ).one()
    finally:
        await engine.dispose()

    assert row.pm_id == "e2e"
    assert row.kind == "llm_call"
    assert float(row.amount_usd) == pytest.approx(0.0042)
    assert row.metadata["model"] == "anthropic/claude-sonnet-4-5"
    assert row.metadata["task_id"] == "e2e-task-1"
    assert row.metadata["tokens_in"] == 7
    assert row.metadata["tokens_out"] == 3
