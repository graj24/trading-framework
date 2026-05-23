"""Tests for ``mode_loader.load_active_overrides`` and the /api/mode endpoint
that consumes it.

Two layers:

  * ``test_api_mode_uses_loaded_overrides`` is a unit test (no DB) that
    monkeypatches the loader to return a fixed override and asserts
    /api/mode reflects it. Catches drift between the loader and the
    endpoint wiring.

  * ``test_load_active_overrides_against_postgres`` (integration-marked)
    spins up Postgres via testcontainers, runs alembic upgrade head,
    INSERTs a row into ``mode_overrides``, and asserts compute_mode
    picks up the override's mode. Catches drift between the migration
    schema and the SELECT this loader issues.
"""

from __future__ import annotations

import contextlib
import os
import subprocess
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import asyncpg
import pytest
from fastapi.testclient import TestClient

from agora.platform.control_plane import app as app_module
from agora.platform.control_plane import state as state_module
from agora.platform.control_plane.mode import Override, compute_mode
from agora.platform.shared.settings import Settings


@pytest.fixture
def settings() -> Settings:
    return Settings(_env_file=None)


@pytest.fixture
def stub_lifespan(monkeypatch: pytest.MonkeyPatch) -> None:
    """Same shape as test_health: keep the lifespan from touching the network."""

    async def fake_pool(settings: Settings) -> object:
        return object()

    async def fake_temporal(settings: Settings) -> object:
        return object()

    def fake_langfuse(settings: Settings) -> None:
        return None

    async def fake_teardown(state: state_module.AppState) -> None:
        with contextlib.suppress(Exception):
            await state.http_client.aclose()

    monkeypatch.setattr(state_module, "_build_pool", fake_pool)
    monkeypatch.setattr(state_module, "_build_temporal_client", fake_temporal)
    monkeypatch.setattr(state_module, "_build_langfuse", fake_langfuse)
    monkeypatch.setattr(state_module, "teardown_app_state", fake_teardown)


def test_api_mode_uses_loaded_overrides(
    settings: Settings, stub_lifespan: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the loader returns a 'trading' override, /api/mode must report 'trading'.

    We pin the override expiry far in the future so it dominates whatever
    clock-driven mode the test machine happens to be in.
    """

    far_future = datetime.now(UTC) + timedelta(days=365)
    forced = Override(mode="trading", expires_at=far_future, requested_at=datetime.now(UTC))

    async def fake_loader(pool: Any, now: datetime) -> list[Override]:
        return [forced]

    # Patch where the endpoint imports it (lazy import inside the handler).
    from agora.platform.control_plane import mode_loader

    monkeypatch.setattr(mode_loader, "load_active_overrides", fake_loader)

    fastapi_app = app_module.create_app(settings)
    with TestClient(fastapi_app) as c:
        r = c.get("/api/mode")
    assert r.status_code == 200
    body = r.json()
    assert body["mode"] == "trading"


def test_api_mode_with_no_overrides_falls_back_to_clock(
    settings: Settings, stub_lifespan: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the loader returns [], the endpoint reports the clock-driven mode."""

    async def fake_loader(pool: Any, now: datetime) -> list[Override]:
        return []

    from agora.platform.control_plane import mode_loader

    monkeypatch.setattr(mode_loader, "load_active_overrides", fake_loader)

    fastapi_app = app_module.create_app(settings)
    with TestClient(fastapi_app) as c:
        r = c.get("/api/mode")
    assert r.status_code == 200
    body = r.json()
    assert body["mode"] in ("build", "trading", "pre_trade_freeze")


# --------------------------------------------------------------------- integration


testcontainers = pytest.importorskip("testcontainers.postgres")
PostgresContainer = testcontainers.PostgresContainer
REPO_2_0 = Path(__file__).resolve().parent.parent


def _run_alembic(args: list[str], pg_url: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["uv", "run", "alembic", *args],
        cwd=REPO_2_0,
        env={"POSTGRES_URL": pg_url, "PATH": os.environ["PATH"]},
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.fixture(scope="module")
def pg_url() -> Iterator[str]:
    with PostgresContainer("postgres:16-alpine", username="agora", password="agora") as pg:
        url = pg.get_connection_url()
        if "+psycopg2" in url:
            url = url.replace("+psycopg2", "")
        up = _run_alembic(["upgrade", "head"], url)
        assert up.returncode == 0, up.stderr
        yield url


@pytest.mark.integration
async def test_load_active_overrides_against_postgres(pg_url: str) -> None:
    """Insert one row into mode_overrides; loader returns it; compute_mode
    selects the override's mode."""
    from agora.platform.control_plane.mode_loader import load_active_overrides

    pool = await asyncpg.create_pool(pg_url, min_size=1, max_size=2)
    assert pool is not None
    try:
        now = datetime.now(UTC)
        # weekend day so the clock-driven mode would be 'build' — the override
        # should win and report 'trading'.
        weekend = datetime(2026, 1, 3, 11, 0, 0, tzinfo=UTC)  # Sat
        async with pool.acquire() as conn:
            await conn.execute("DELETE FROM mode_overrides")
            await conn.execute(
                """
                INSERT INTO mode_overrides (requested_at, mode, expires_at, reason)
                VALUES ($1, $2, $3, $4)
                """,
                now,
                "trading",
                now + timedelta(hours=1),
                "test override",
            )

        overrides = await load_active_overrides(pool, now)
        assert len(overrides) == 1
        assert overrides[0].mode == "trading"

        # Pass the override list through the controller — it should win even
        # though `weekend` falls on a Saturday.
        result = compute_mode(weekend, overrides=overrides)
        assert result.mode == "trading"

        # Cleanup so the table is empty again — leaves the next test alone.
        async with pool.acquire() as conn:
            await conn.execute("DELETE FROM mode_overrides")
    finally:
        await pool.close()
