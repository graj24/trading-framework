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
        return _FakeTemporalClient()

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


class _FakeWorkflowHandle:
    """Records signal invocations.

    The endpoint awaits ``handle.signal(PMSupervisor.stop)`` with the
    decorated method object; Temporal accepts either the bound method
    or a string name. We record by ``__name__`` for ergonomic asserts
    in the tests below.
    """

    def __init__(self) -> None:
        self.signals: list[str] = []
        self.raise_on_signal: Exception | None = None

    async def signal(self, signal_method: Any, *args: Any) -> None:
        if self.raise_on_signal is not None:
            raise self.raise_on_signal
        name = getattr(signal_method, "__name__", str(signal_method))
        self.signals.append(name)


class _FakeTemporalClient:
    """Records start_workflow invocations without touching a real cluster.

    The endpoint only awaits ``start_workflow`` and never inspects the
    return value, so we return ``None`` and stash the call args for any
    test that wants to assert on them.

    For Step 2.3, also returns ``_FakeWorkflowHandle`` instances from
    ``get_workflow_handle`` so signal dispatch is observable.
    """

    def __init__(self) -> None:
        self.started: list[dict[str, Any]] = []
        self.handles: dict[str, _FakeWorkflowHandle] = {}

    async def start_workflow(
        self,
        workflow_fn: Any,
        *args: Any,
        id: str,
        task_queue: str,
        **kwargs: Any,
    ) -> None:
        self.started.append(
            {
                "workflow": workflow_fn,
                "args": args,
                "id": id,
                "task_queue": task_queue,
                "kwargs": kwargs,
            }
        )
        return None

    def get_workflow_handle(self, workflow_id: str) -> _FakeWorkflowHandle:
        if workflow_id not in self.handles:
            self.handles[workflow_id] = _FakeWorkflowHandle()
        return self.handles[workflow_id]


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
        "update_workflow_id": [],
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

    async def fake_update_workflow_id(pool: Any, pm_id: str, workflow_id: str | None) -> None:
        calls["update_workflow_id"].append((pm_id, workflow_id))

    async def fake_get_pm(pool: Any, pm_id: str) -> pm_repo_module.PMRecord | None:
        calls["get"].append(pm_id)
        return get_record

    async def fake_list_pms(pool: Any) -> list[pm_repo_module.PMSummary]:
        calls["list"].append(pool)
        return list_records or []

    monkeypatch.setattr(pm_repo_module, "pm_exists", fake_pm_exists)
    monkeypatch.setattr(pm_repo_module, "insert_pm", fake_insert_pm)
    monkeypatch.setattr(pm_repo_module, "update_pm_status", fake_update_status)
    monkeypatch.setattr(pm_repo_module, "update_pm_workflow_id", fake_update_workflow_id)
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
    assert body["workflow_id"] == "pm-pm1"
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

    # DB call ordering: exists check, insert, update to spawned, update workflow_id.
    assert calls["exists"] == ["pm1"]
    assert len(calls["insert"]) == 1
    assert calls["insert"][0]["pm_id"] == "pm1"
    assert calls["insert"][0]["name"] == "PM1"
    assert calls["insert"][0]["prompt_path"] == "/dev/null"
    assert calls["update_status"] == [("pm1", "spawned")]
    assert calls["update_workflow_id"] == [("pm1", "pm-pm1")]


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


def test_spawn_503_when_temporal_client_unavailable(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Spawn returns 503 when Temporal was unreachable at app startup."""

    async def no_temporal(settings: Settings) -> None:
        return None

    monkeypatch.setattr(state_module, "_build_temporal_client", no_temporal)
    fastapi_app = app_module.create_app(settings)
    with TestClient(fastapi_app) as c:
        r = c.post(
            "/api/pms/spawn",
            json={"name": "PM1", "starting_capital_inr": 100.0},
        )
    assert r.status_code == 503
    assert "temporal" in r.json()["detail"].lower()


def test_spawn_starts_workflow_with_correct_args(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The spawn endpoint must hand the right config to start_workflow."""
    from agora.platform.workers.pm_supervisor import PMConfig, PMSupervisor

    _stub_repo(monkeypatch, exists=False)
    # Reach into the started TestClient's app.state to inspect the fake.
    r = client.post(
        "/api/pms/spawn",
        json={"name": "PM1", "starting_capital_inr": 1_000_000.0},
    )
    assert r.status_code == 200, r.text

    fake = client.app.state.agora.temporal_client  # type: ignore[attr-defined]
    assert isinstance(fake, _FakeTemporalClient)
    assert len(fake.started) == 1
    started = fake.started[0]
    assert started["workflow"] is PMSupervisor.run
    assert started["id"] == "pm-pm1"
    assert started["task_queue"] == "agora"
    # Single positional arg: the PMConfig dataclass.
    (config,) = started["args"]
    assert isinstance(config, PMConfig)
    assert config.pm_id == "pm1"
    assert config.name == "PM1"
    assert config.starting_capital_inr == 1_000_000.0


def test_spawn_marks_error_when_start_workflow_raises(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If start_workflow blows up, the PM is marked 'error' and 500 returned."""
    calls = _stub_repo(monkeypatch, exists=False)

    async def boom(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("temporal exploded")

    # Patch the fake client's start_workflow on the live app instance.
    client.app.state.agora.temporal_client.start_workflow = boom  # type: ignore[attr-defined]

    r = client.post(
        "/api/pms/spawn",
        json={"name": "PM1", "starting_capital_inr": 1_000_000.0},
    )
    assert r.status_code == 500
    # spawned -> then error after start_workflow blew up.
    assert calls["update_status"] == [("pm1", "spawned"), ("pm1", "error")]
    # workflow_id should NOT have been persisted since start_workflow failed.
    assert calls["update_workflow_id"] == []


# --------------------------------------------------------- stop / pause / resume
# Step 2.3 endpoints. These extend the same TestClient harness above; the
# fake Temporal client now also records signal calls via _FakeWorkflowHandle.


def _make_pm_record(
    pm_id: str = "pm1",
    *,
    status: str = "running",
    workflow_id: str | None = "pm-pm1",
) -> pm_repo_module.PMRecord:
    """Helper: build a PMRecord with sensible defaults for state-change tests."""
    return pm_repo_module.PMRecord(
        id=pm_id,
        name=pm_id.upper(),
        status=status,
        starting_capital_inr=1_000_000.0,
        spawned_at=datetime(2026, 1, 1, tzinfo=UTC),
        stopped_at=None,
        prompt_path="/dev/null",
        config={},
        workflow_id=workflow_id,
    )


def _fake_temporal(client: TestClient) -> _FakeTemporalClient:
    fake = client.app.state.agora.temporal_client  # type: ignore[attr-defined]
    assert isinstance(fake, _FakeTemporalClient)
    return fake


# ---------- stop


def test_stop_404_when_pm_missing(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_repo(monkeypatch, get_record=None)
    r = client.post("/api/pms/missing/stop")
    assert r.status_code == 404


def test_stop_409_when_workflow_id_null(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_repo(monkeypatch, get_record=_make_pm_record(workflow_id=None))
    r = client.post("/api/pms/pm1/stop")
    assert r.status_code == 409
    assert "workflow_id" in r.json()["detail"]


def test_stop_signals_workflow_and_marks_stopped(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _stub_repo(monkeypatch, get_record=_make_pm_record(status="running"))
    r = client.post("/api/pms/pm1/stop")
    assert r.status_code == 200, r.text
    assert r.json() == {"pm_id": "pm1", "status": "stopped"}

    fake = _fake_temporal(client)
    handle = fake.handles["pm-pm1"]
    assert handle.signals == ["stop"]
    assert calls["update_status"] == [("pm1", "stopped")]


def test_stop_idempotent_when_already_stopped(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Already-stopped PM short-circuits to 200 without re-signalling."""
    calls = _stub_repo(monkeypatch, get_record=_make_pm_record(status="stopped"))
    r = client.post("/api/pms/pm1/stop")
    assert r.status_code == 200
    assert r.json() == {"pm_id": "pm1", "status": "stopped"}

    fake = _fake_temporal(client)
    # No signal sent, no DB write — pure short-circuit.
    assert "pm-pm1" not in fake.handles or fake.handles["pm-pm1"].signals == []
    assert calls["update_status"] == []


# ---------- pause


def test_pause_signals_and_marks_paused(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _stub_repo(monkeypatch, get_record=_make_pm_record(status="running"))
    r = client.post("/api/pms/pm1/pause")
    assert r.status_code == 200, r.text
    assert r.json() == {"pm_id": "pm1", "status": "paused"}

    fake = _fake_temporal(client)
    assert fake.handles["pm-pm1"].signals == ["pause"]
    assert calls["update_status"] == [("pm1", "paused")]


def test_pause_409_when_stopped(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _stub_repo(monkeypatch, get_record=_make_pm_record(status="stopped"))
    r = client.post("/api/pms/pm1/pause")
    assert r.status_code == 409
    assert "stopped" in r.json()["detail"]
    assert calls["update_status"] == []


def test_pause_idempotent_when_paused(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _stub_repo(monkeypatch, get_record=_make_pm_record(status="paused"))
    r = client.post("/api/pms/pm1/pause")
    assert r.status_code == 200
    assert r.json() == {"pm_id": "pm1", "status": "paused"}

    fake = _fake_temporal(client)
    assert "pm-pm1" not in fake.handles or fake.handles["pm-pm1"].signals == []
    assert calls["update_status"] == []


# ---------- resume


def test_resume_signals_and_marks_running(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _stub_repo(monkeypatch, get_record=_make_pm_record(status="paused"))
    r = client.post("/api/pms/pm1/resume")
    assert r.status_code == 200, r.text
    assert r.json() == {"pm_id": "pm1", "status": "running"}

    fake = _fake_temporal(client)
    assert fake.handles["pm-pm1"].signals == ["resume"]
    assert calls["update_status"] == [("pm1", "running")]


def test_resume_409_when_running(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """Resume from running is rejected — there's nothing to resume."""
    calls = _stub_repo(monkeypatch, get_record=_make_pm_record(status="running"))
    r = client.post("/api/pms/pm1/resume")
    assert r.status_code == 409
    assert "paused" in r.json()["detail"]
    assert calls["update_status"] == []


def test_resume_409_when_stopped(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _stub_repo(monkeypatch, get_record=_make_pm_record(status="stopped"))
    r = client.post("/api/pms/pm1/resume")
    assert r.status_code == 409
    assert "stopped" in r.json()["detail"]
    assert calls["update_status"] == []


# ---------- 503 envelope


def test_state_change_endpoints_503_when_pool_is_none(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """All three state-change endpoints return 503 when the pool is None."""

    async def no_pool(settings: Settings) -> None:
        return None

    monkeypatch.setattr(state_module, "_build_pool", no_pool)
    fastapi_app = app_module.create_app(settings)
    with TestClient(fastapi_app) as c:
        r_stop = c.post("/api/pms/pm1/stop")
        r_pause = c.post("/api/pms/pm1/pause")
        r_resume = c.post("/api/pms/pm1/resume")
    assert r_stop.status_code == 503
    assert r_pause.status_code == 503
    assert r_resume.status_code == 503


def test_state_change_endpoints_503_when_temporal_is_none(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """All three state-change endpoints return 503 when temporal_client is None."""

    async def no_temporal(settings: Settings) -> None:
        return None

    monkeypatch.setattr(state_module, "_build_temporal_client", no_temporal)
    fastapi_app = app_module.create_app(settings)
    with TestClient(fastapi_app) as c:
        r_stop = c.post("/api/pms/pm1/stop")
        r_pause = c.post("/api/pms/pm1/pause")
        r_resume = c.post("/api/pms/pm1/resume")
    assert r_stop.status_code == 503
    assert "temporal" in r_stop.json()["detail"].lower()
    assert r_pause.status_code == 503
    assert r_resume.status_code == 503


# ----------------------------------------------------------------- journal
# Step 2.4 — GET /api/pms/{id}/journal. PM repo is stubbed (DB-free), the
# workspace lives under tmp_path so we exercise read_journal_tail end-to-end.


def _today_journal(workspace_root: Path, pm_id: str) -> Path:
    """Return the path to today's journal file (UTC). Helper for the tests
    below — must agree with read_journal_tail's date selection."""
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    return workspace_root / pm_id / "journals" / f"{today}.md"


def test_get_journal_404_when_pm_missing(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_repo(monkeypatch, get_record=None)
    r = client.get("/api/pms/missing/journal")
    assert r.status_code == 404


def test_get_journal_returns_empty_when_no_journal_file(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """PM exists in DB but the journal file hasn't been created yet."""
    _stub_repo(monkeypatch, get_record=_make_pm_record())
    # Journal directory absent — read_journal_tail must return [] cleanly.
    r = client.get("/api/pms/pm1/journal")
    assert r.status_code == 200
    body = r.json()
    assert body == {"pm_id": "pm1", "lines": []}


def test_get_journal_returns_tail(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Write 100 lines, request the last 10."""
    _stub_repo(monkeypatch, get_record=_make_pm_record())
    journal = _today_journal(tmp_path, "pm1")
    journal.parent.mkdir(parents=True, exist_ok=True)
    journal.write_text("".join(f"line {i}\n" for i in range(100)), encoding="utf-8")

    r = client.get("/api/pms/pm1/journal", params={"lines": 10})
    assert r.status_code == 200
    body = r.json()
    assert body["pm_id"] == "pm1"
    assert body["lines"] == [f"line {i}" for i in range(90, 100)]


def test_get_journal_caps_at_500(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """lines=10000 must be rejected with 422 before any I/O happens."""
    _stub_repo(monkeypatch, get_record=_make_pm_record())
    r = client.get("/api/pms/pm1/journal", params={"lines": 10000})
    assert r.status_code == 422
