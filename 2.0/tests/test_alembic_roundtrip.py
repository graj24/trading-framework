"""Round-trip alembic upgrade/downgrade against a fresh Postgres container.

This test catches non-reversible migrations early — adding a column without a
matching drop in `downgrade()` is the most common shape of bug here. It also
satisfies the K1 requirement that downgrades work.

Slow on first run because testcontainers pulls the postgres image. Subsequent
runs are quick. Marked with `slow` in case a future test config wants to skip.
"""

from __future__ import annotations

import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

# `testcontainers` is in dev deps. Importing inside the test would also work,
# but doing it at module scope makes the dependency explicit.
testcontainers = pytest.importorskip("testcontainers.postgres")
PostgresContainer = testcontainers.PostgresContainer

REPO_2_0 = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def pg_url() -> Iterator[str]:
    """Spin up a one-shot Postgres container; yield its sync URL."""
    with PostgresContainer("postgres:16-alpine", username="agora", password="agora") as pg:
        # testcontainers gives us postgresql+psycopg2://...; the alembic env.py
        # rewrites both psycopg2 and asyncpg to psycopg3, but we feed a plain
        # postgresql:// URL so the rewriting is exercised.
        url = pg.get_connection_url()
        if "+psycopg2" in url:
            url = url.replace("+psycopg2", "")
        yield url


def _run_alembic(args: list[str], pg_url: str) -> subprocess.CompletedProcess[str]:
    """Invoke the project's alembic CLI against the given DB."""
    return subprocess.run(
        ["uv", "run", "alembic", *args],
        cwd=REPO_2_0,
        env={"POSTGRES_URL": pg_url, "PATH": __import__("os").environ["PATH"]},
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.slow
def test_alembic_roundtrip(pg_url: str) -> None:
    up1 = _run_alembic(["upgrade", "head"], pg_url)
    assert up1.returncode == 0, up1.stderr

    down = _run_alembic(["downgrade", "base"], pg_url)
    assert down.returncode == 0, down.stderr

    up2 = _run_alembic(["upgrade", "head"], pg_url)
    assert up2.returncode == 0, up2.stderr
