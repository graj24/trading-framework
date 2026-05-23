"""FastAPI endpoint tests for /api/pms/*.

The pm_repo helpers and the lifespan resource builders are monkey-patched
so the endpoint tests don't require a real Postgres. The provisioning
helper is exercised against ``tmp_path`` (real filesystem, fake DB) so
we cover the response shape and the actual workspace tree.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from agora.platform.control_plane import app as app_module
from agora.platform.control_plane import pm_repo as pm_repo_module
from agora.platform.control_plane import state as state_module
from agora.platform.shared.settings import Settings

# A sentinel value the stubbed builder returns in place of a real asyncpg.Pool.
# pm_repo functions are monkeypatched, so they never actually call .acquire().
_SENTINEL_POOL: Any = object()


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(_env_file=None, workspace_root=str(tmp_path))


@pytest.fixture(autouse=True)
def stub_lifespan_resources(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace lifespan builders so the app boots without network."""

    async def fake_pool(settings: Settings) -> Any:
        return _SENTINEL_POOL

    async def fake_temporal(settings: Settings) -> object:
        return object()

    def fake_langfuse(settings: Settings) -> None:
        return None

    async def fake_teardown(state: state_module.AppState) -> None:
        import contextlib

        with contextlib.suppress(Exception):
            await state.http_client.aclose()

    monkeypatch.setattr(state_module, "_build_pool", fake_pool)
    monkeypatch.setattr(state_module, "_build_temporal_client", fake_temporal)
    monkeypatch.setattr(state_module, "_build_langfuse", fake_langfuse)
    monkeypatch.setattr(state_module, "teardown_app_state", fake_teardown)


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    fastapi_app = app_module.create_app(settings)
    with TestClient(fastapi_app) as c:
        yield c


def _stub_repo(
    monkeypatch: pytest.MonkeyPatch,
    *,
    exists: bool = False,
    get_record: pm_repo_module.PMRecord | None = None,
    list_records: list[pm_repo_module.PMSummary] | None = None,
    insert_raises: Exception | None = None,
) -> dict[str, list[Any]]:
    """Patch pm_repo.* used by the endpoint, return a calls log."""
    calls: dict[str, list[Any]] = {
        "insert": [],
        "update_status": [],
        "exists": [],
        "get": [],
        "list": [],
    }

    async def fake_pm_exists(pool: Any, pm_id: str) -> bool:
        calls["exists"].append(pm_id)
        return exists

    async def fake_insert_pm(pool: Any, **kwargs: Any) -> None:
        calls["insert"].append(kwargs)
        if insert_raises is not None:
            raise insert_raises

    async def fake_update_status(pool: Any, pm_id: str, status: str) -> None:
        calls["update_status"].append((pm_id, status))

    async def fake_get_pm(pool: Any, pm_id: str) -> pm_repo_module.PMRecord | None:
        calls["get"].append(pm_id)
        return get_record

    async def fake_list_pms(pool: Any) -> list[pm_repo_module.PMSummary]:
        calls["list"].append(pool)
        return list_records or []

    monkeypatch.setattr(pm_repo_module, "pm_exists", fake_pm_exists)
    monkeypatch.setattr(pm_repo_module, "insert_pm", fake_insert_pm)
    monkeypatch.setattr(pm_repo_module, "update_pm_status", fake_update_status)
    monkeypatch.setattr(pm_repo_module, "get_pm", fake_get_pm)
    monkeypatch.setattr(pm_repo_module, "list_pms", fake_list_pms)
    return calls


@pytest.mark.parametrize(
    "bad_name",
    [
        "x",  # too short
        "1pm",  # leading digit
        "pm@1",  # invalid char
        "p" * 33,  # too long
        " PM",  # leading space
    ],
)
def test_spawn_validates_name(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, bad_name: str
) -> None:
    _stub_repo(monkeypatch)
    r = client.post(
        "/api/pms/spawn",
        json={"name": bad_name, "starting_capital_inr": 100.0},
    )
    assert r.status_code == 422, r.text


@pytest.mark.parametrize("bad_capital", [0, -1, -1000.0, 2e9])
def test_spawn_validates_capital(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, bad_capital: float
) -> None:
    _stub_repo(monkeypatch)
    r = client.post(
        "/api/pms/spawn",
        json={"name": "PM1", "starting_capital_inr": bad_capital},
    )
    assert r.status_code == 422, r.text


def test_spawn_creates_pm_and_workspace(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls = _stub_repo(monkeypatch, exists=False)
    r = client.post(
        "/api/pms/spawn",
        json={"name": "PM1", "starting_capital_inr": 1_000_000.0},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["pm_id"] == "pm1"
    assert body["workflow_id"] is None
    assert body["status"] == "spawned"
    workspace = Path(body["workspace_path"])
    assert workspace.is_dir()
    assert workspace.name == "pm1"
    # The settings fixture pinned workspace_root to tmp_path; provisioning
    # should land underneath it.
    assert str(workspace).startswith(str(tmp_path.resolve()))
    for sub in ("plans", "journals", "strategies", "research", "code"):
        assert (workspace / sub).is_dir()
    assert (workspace / "config.yaml").is_file()

    # DB call ordering: exists check, insert, update to spawned.
    assert calls["exists"] == ["pm1"]
    assert len(calls["insert"]) == 1
    assert calls["insert"][0]["pm_id"] == "pm1"
    assert calls["insert"][0]["name"] == "PM1"
    assert calls["insert"][0]["prompt_path"] == "/dev/null"
    assert calls["update_status"] == [("pm1", "spawned")]


def test_spawn_normalizes_pm_id(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """name='PM 1' → pm_id='pm_1'; name='PM-1' → pm_id='pm_1'."""
    calls = _stub_repo(monkeypatch, exists=False)
    r = client.post(
        "/api/pms/spawn",
        json={"name": "PM 1", "starting_capital_inr": 100.0},
    )
    assert r.status_code == 200, r.text
    assert r.json()["pm_id"] == "pm_1"
    assert calls["insert"][0]["pm_id"] == "pm_1"


def test_spawn_409_on_duplicate(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _stub_repo(monkeypatch, exists=True)
    r = client.post(
        "/api/pms/spawn",
        json={"name": "PM1", "starting_capital_inr": 1_000_000.0},
    )
    assert r.status_code == 409
    assert "already exists" in r.json()["detail"]
    assert calls["insert"] == []  # no insert when duplicate


def test_get_pm_404_when_missing(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_repo(monkeypatch, get_record=None)
    r = client.get("/api/pms/missing")
    assert r.status_code == 404


def test_get_pm_returns_record(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    record = pm_repo_module.PMRecord(
        id="pm1",
        name="PM1",
        status="spawned",
        starting_capital_inr=1_000_000.0,
        spawned_at=datetime(2026, 1, 1, tzinfo=UTC),
        stopped_at=None,
        prompt_path="/dev/null",
        config={"foo": "bar"},
        workflow_id=None,
    )
    _stub_repo(monkeypatch, get_record=record)
    r = client.get("/api/pms/pm1")
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == "pm1"
    assert body["status"] == "spawned"
    assert body["starting_capital_inr"] == 1_000_000.0
    assert body["config"] == {"foo": "bar"}
    assert body["workflow_id"] is None


def test_list_pms_returns_summaries(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    summaries = [
        pm_repo_module.PMSummary(id="pm1", name="PM1", status="spawned"),
        pm_repo_module.PMSummary(id="pm2", name="PM2", status="running"),
    ]
    _stub_repo(monkeypatch, list_records=summaries)
    r = client.get("/api/pms")
    assert r.status_code == 200
    assert r.json() == [
        {"id": "pm1", "name": "PM1", "status": "spawned"},
        {"id": "pm2", "name": "PM2", "status": "running"},
    ]


def test_503_when_pool_is_none_for_db_endpoints(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """All DB-touching endpoints return 503 when the pool is None."""

    async def no_pool(settings: Settings) -> None:
        return None

    monkeypatch.setattr(state_module, "_build_pool", no_pool)
    fastapi_app = app_module.create_app(settings)
    with TestClient(fastapi_app) as c:
        r_list = c.get("/api/pms")
        r_get = c.get("/api/pms/pm1")
        r_spawn = c.post(
            "/api/pms/spawn",
            json={"name": "PM1", "starting_capital_inr": 100.0},
        )
        r_mode = c.get("/api/mode")
    assert r_list.status_code == 503
    assert r_get.status_code == 503
    assert r_spawn.status_code == 503
    assert r_mode.status_code == 503
