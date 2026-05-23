"""K2 Step 2.6 — the architectural lock for tree-rooted lifecycle.

Proves that:
  - The PMSupervisor workflow's lifecycle is owned by Temporal, not by
    the worker process.
  - SIGKILL'ing the worker mid-cycle does NOT lose the workflow.
  - A fresh worker pointed at the same Temporal cluster picks up the
    pending workflow and continues from where it left off.

If this test fails, do not move past K2: the whole architecture rests
on Temporal's durability guarantee (plan/01-KEYSTONE.md §4 Step 2.6).

Test environment — Option A (chosen)
-----------------------------------
We spin up a one-shot Postgres testcontainer plus a local Temporal
dev server (``WorkflowEnvironment.start_local``) so the test is
reproducible without ``make up`` or any other operator setup. Workers
are real subprocesses (``python -m agora.platform.workers.main``) so
``os.kill(pid, SIGKILL)`` reflects an actual crash, not a graceful
``Worker.shutdown``. ``start_local`` exposes its address via
``env._server.target`` (and the underlying ``client.service_client.config``);
we pass that into the worker subprocess via ``TEMPORAL_HOST``.

Marked ``integration`` and ``slow`` because it takes ~90 seconds of
real wallclock to observe heartbeats across a kill + restart, even
with the cycle tightened to 10 seconds. Deselected from ``make test``;
runs in ``make test-all`` (``pytest -m ''``).
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import subprocess
import sys
import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from temporalio.client import Client
from temporalio.testing import WorkflowEnvironment

from agora.platform.workers.pm_supervisor import PMConfig, PMSupervisor

testcontainers = pytest.importorskip("testcontainers.postgres")
PostgresContainer = testcontainers.PostgresContainer

pytestmark = [pytest.mark.integration, pytest.mark.slow]

REPO_2_0 = Path(__file__).resolve().parent.parent
TASK_QUEUE = "agora"

# Cycle is tight enough to observe two crossings of the kill+restart
# boundary inside a reasonable test budget, but still real wallclock —
# the property under test is durability across a process death, not
# speed. 10s x 4-5 cycles = ~45s of useful runtime.
CYCLE_SECONDS = 10
DEAD_BUDGET_SECONDS = 18  # > 1 cycle, so a missed heartbeat is detectable
RESUME_BUDGET_SECONDS = 30  # > 2 cycles + sticky-queue reassign delay


# ----------------------------------------------------------------- fixtures


@pytest.fixture(scope="module")
def pg_url() -> Iterator[str]:
    """Spin up a Postgres testcontainer and run alembic upgrade head.

    The PMSupervisor's activities call into ``pm_repo`` and ``mode_loader``
    which expect the AGORA schema to be present. We migrate the same way
    ``make db-migrate`` does — through the project's alembic CLI — so the
    test exercises the production migration path.
    """
    with PostgresContainer("postgres:16-alpine", username="agora", password="agora") as pg:
        url = pg.get_connection_url()
        if "+psycopg2" in url:
            url = url.replace("+psycopg2", "")
        # alembic env.py rewrites the URL for psycopg3; pass the bare URL.
        result = subprocess.run(
            ["uv", "run", "alembic", "upgrade", "head"],
            cwd=REPO_2_0,
            env={
                "POSTGRES_URL": url,
                "PATH": os.environ["PATH"],
                "HOME": os.environ.get("HOME", "/tmp"),
            },
            capture_output=True,
            text=True,
            check=False,
        )
        assert (
            result.returncode == 0
        ), f"alembic upgrade failed: stdout={result.stdout!r} stderr={result.stderr!r}"
        # Convert to the asyncpg URL the worker expects.
        asyncpg_url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
        yield asyncpg_url


@pytest.fixture
async def temporal_env() -> AsyncIterator[WorkflowEnvironment]:
    """Local Temporal dev server. Per-test so each crash run is isolated."""
    env = await WorkflowEnvironment.start_local()
    try:
        yield env
    finally:
        await env.shutdown()


def _temporal_target(env: WorkflowEnvironment) -> str:
    """Extract the host:port the dev server bound to."""
    # Public-ish path: the bridge EphemeralServer is stored on
    # _EphemeralServerWorkflowEnvironment as ``_server``; ``server.target``
    # is the documented frontend address. The client also carries the
    # same value in ``service_client.config.target_host``; we prefer
    # the latter since it's part of the client's public config.
    target: str = env.client.service_client.config.target_host
    return target


# ------------------------------------------------------------ subprocess


def _start_worker_subprocess(
    *,
    temporal_host: str,
    postgres_url: str,
    workspace_root: Path,
    log_path: Path,
) -> subprocess.Popen[bytes]:
    """Launch the AGORA worker as a subprocess.

    Stdout + stderr are captured to ``log_path`` (combined) for post-mortem
    inspection on test failure. The subprocess inherits the parent's PATH
    + UV_* settings so ``uv run`` finds the project venv. We deliberately
    ``setsid`` on POSIX so SIGKILL on the leader takes the whole group
    down — Temporal's worker has its own internal task tree.
    """
    env = {
        **os.environ,
        "TEMPORAL_HOST": temporal_host,
        "TEMPORAL_NAMESPACE": "default",
        "POSTGRES_URL": postgres_url,
        "WORKSPACE_ROOT": str(workspace_root),
        "AGORA_LOG_FORMAT": "human",
        # Defensive: clear any langfuse keys so the worker doesn't try to
        # phone home from a test process.
        "LANGFUSE_PUBLIC_KEY": "",
        "LANGFUSE_SECRET_KEY": "",
        # Settings is @lru_cache'd; subprocess is fresh, no concern.
        "PYTHONUNBUFFERED": "1",
    }
    log_fh = log_path.open("ab")
    popen_kwargs: dict[str, object] = {
        "cwd": str(REPO_2_0),
        "env": env,
        "stdout": log_fh,
        "stderr": subprocess.STDOUT,
        "close_fds": True,
    }
    if sys.platform != "win32":
        # Own process group: lets us SIGKILL the whole tree if the
        # worker forks anything. ``uv run`` itself spawns a Python child.
        popen_kwargs["start_new_session"] = True
    proc: subprocess.Popen[bytes] = subprocess.Popen(  # type: ignore[call-overload]
        ["uv", "run", "python", "-m", "agora.platform.workers.main"],
        **popen_kwargs,
    )
    return proc


async def _wait_for_worker_ready(
    proc: subprocess.Popen[bytes],
    log_path: Path,
    *,
    timeout: float,
) -> None:
    """Poll the worker's log file until it logs the 'starting' line.

    The worker logs ``worker starting on task_queue=agora`` from
    ``main.py`` immediately before ``await worker.run()``. That marker
    is enough to know the gRPC connection to Temporal succeeded and the
    sticky task queue is being polled.
    """
    deadline = asyncio.get_event_loop().time() + timeout
    needle = b"worker starting on task_queue="
    while asyncio.get_event_loop().time() < deadline:
        if proc.poll() is not None:
            tail = log_path.read_bytes()[-2000:].decode(errors="replace")
            raise AssertionError(
                f"worker subprocess exited before becoming ready: "
                f"rc={proc.returncode} log_tail={tail!r}"
            )
        if log_path.exists() and needle in log_path.read_bytes():
            return
        await asyncio.sleep(0.25)
    tail = log_path.read_bytes()[-2000:].decode(errors="replace") if log_path.exists() else ""
    raise AssertionError(f"worker did not become ready within {timeout}s; log_tail={tail!r}")


def _sigkill_worker(proc: subprocess.Popen[bytes]) -> None:
    """SIGKILL the worker's whole process group; wait for the leader."""
    if proc.poll() is not None:
        return
    if sys.platform != "win32":
        # ``contextlib.suppress`` reads cleaner than try/except/pass and
        # ProcessLookupError is the only race we tolerate here.
        with contextlib.suppress(ProcessLookupError):
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    else:  # pragma: no cover — tests target POSIX
        proc.kill()
    proc.wait(timeout=10)


def _terminate_worker(proc: subprocess.Popen[bytes]) -> None:
    """Best-effort graceful shutdown for clean teardown only."""
    if proc.poll() is not None:
        return
    try:
        if sys.platform != "win32":
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        else:  # pragma: no cover
            proc.terminate()
        proc.wait(timeout=10)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        _sigkill_worker(proc)


# ------------------------------------------------------------- journal


def _read_journal_lines(path: Path) -> list[str]:
    """Return the journal's lines, treating a missing file as empty."""
    if not path.exists():
        return []
    return path.read_text(encoding="utf-8").splitlines()


async def _wait_for_journal_lines(
    path: Path,
    *,
    expected: int,
    timeout: float,
) -> list[str]:
    """Poll the journal until at least ``expected`` lines exist."""
    deadline = asyncio.get_event_loop().time() + timeout
    last: list[str] = []
    while asyncio.get_event_loop().time() < deadline:
        last = _read_journal_lines(path)
        if len(last) >= expected:
            return last
        await asyncio.sleep(0.5)
    raise AssertionError(
        f"journal at {path} did not reach {expected} lines within {timeout}s; "
        f"observed {len(last)} lines"
    )


# ---------------------------------------------------------------- the test


async def test_workflow_survives_worker_crash(
    pg_url: str,
    temporal_env: WorkflowEnvironment,
    tmp_path: Path,
) -> None:
    """SIGKILL the worker mid-cycle; a restart resumes the same workflow."""
    temporal_host = _temporal_target(temporal_env)
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    test_pm_id = f"crash_{uuid.uuid4().hex[:8]}"
    test_workflow_id = f"pm-{test_pm_id}"

    # ----- 1) Insert the pms row that mark_pm_running will UPDATE.
    # The supervisor doesn't insert; it expects the row to already exist
    # (the API path inserts on POST /api/pms). For this test we go around
    # the API and write the row directly, mirroring what insert_pm does.
    import asyncpg  # local import keeps test_pm_supervisor's module-top clean

    bare_pg_url = pg_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    conn = await asyncpg.connect(bare_pg_url)
    try:
        await conn.execute(
            """
            INSERT INTO pms (id, name, status, starting_capital_inr, prompt_path, config)
            VALUES ($1, $2, 'spawned', $3, $4, $5::jsonb)
            """,
            test_pm_id,
            test_pm_id,
            1.0,
            f"/tmp/{test_pm_id}/prompt.md",
            "{}",
        )
    finally:
        await conn.close()

    # ----- 2) First worker comes up.
    log1 = tmp_path / "worker1.log"
    worker1 = _start_worker_subprocess(
        temporal_host=temporal_host,
        postgres_url=pg_url,
        workspace_root=workspace_root,
        log_path=log1,
    )
    try:
        await _wait_for_worker_ready(worker1, log1, timeout=45)

        # ----- 3) Spawn the workflow directly via the env's client.
        client = await Client.connect(temporal_host, namespace="default")
        config = PMConfig(
            pm_id=test_pm_id,
            name=test_pm_id,
            starting_capital_inr=1.0,
            build_cycle_seconds=CYCLE_SECONDS,
            trading_cycle_seconds=CYCLE_SECONDS,
            paused_poll_seconds=2,
        )
        handle = await client.start_workflow(
            PMSupervisor.run,
            config,
            id=test_workflow_id,
            task_queue=TASK_QUEUE,
        )

        today = datetime.now(UTC).strftime("%Y-%m-%d")
        journal_path = workspace_root / test_pm_id / "journals" / f"{today}.md"

        # ----- 4) Wait for the first heartbeat to land — proves the
        # workflow is actually running, not just scheduled.
        lines_before_kill = await _wait_for_journal_lines(journal_path, expected=1, timeout=60)
        assert lines_before_kill, "expected at least one heartbeat before kill"

        # ----- 5) SIGKILL the worker. This must NOT touch Temporal.
        _sigkill_worker(worker1)
        assert (
            worker1.returncode is not None and worker1.returncode != 0
        ), f"worker should have been killed, got rc={worker1.returncode}"

        # ----- 6) Confirm no new heartbeats accrue while the worker is
        # dead. We sleep > one cycle; if Temporal were running activities
        # in some out-of-process daemon (it isn't), heartbeats would
        # leak through here.
        await asyncio.sleep(DEAD_BUDGET_SECONDS)
        lines_during_dead = _read_journal_lines(journal_path)
        assert len(lines_during_dead) == len(lines_before_kill), (
            f"workflow should not produce heartbeats with no worker; "
            f"before={len(lines_before_kill)} after_dead={len(lines_during_dead)}; "
            f"new_lines={lines_during_dead[len(lines_before_kill):]}"
        )

        # ----- 7) Fresh worker, same Temporal cluster.
        log2 = tmp_path / "worker2.log"
        worker2 = _start_worker_subprocess(
            temporal_host=temporal_host,
            postgres_url=pg_url,
            workspace_root=workspace_root,
            log_path=log2,
        )
        try:
            await _wait_for_worker_ready(worker2, log2, timeout=45)

            # ----- 8) Heartbeats must resume. Budget is generous to
            # cover sticky-queue reassign (~5s default) + one full cycle.
            await asyncio.sleep(RESUME_BUDGET_SECONDS)
            lines_after_restart = _read_journal_lines(journal_path)
            assert len(lines_after_restart) > len(lines_during_dead), (
                f"workflow must resume after worker restart; "
                f"before_kill={len(lines_before_kill)} "
                f"after_dead={len(lines_during_dead)} "
                f"after_restart={len(lines_after_restart)}\n"
                f"worker2 log tail:\n"
                f"{log2.read_text(errors='replace')[-2000:]}"
            )

            # ----- 9) Clean stop.
            await handle.signal(PMSupervisor.stop)
            try:
                await asyncio.wait_for(handle.result(), timeout=30)
            except TimeoutError:
                pytest.fail(
                    "workflow did not exit within 30s after stop signal; "
                    f"worker2 log tail:\n{log2.read_text(errors='replace')[-2000:]}"
                )
        finally:
            _terminate_worker(worker2)
    finally:
        # Belt-and-braces: if anything above raised, ensure the first
        # worker is gone too.
        _sigkill_worker(worker1)
