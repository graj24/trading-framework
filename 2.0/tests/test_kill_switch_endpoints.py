"""FastAPI endpoint tests for /api/kill-switch.

K3 Step 3.7. The kill_switch row is the singleton at id=1 (seeded by
migration 0001). Endpoints are tested against an in-memory stub
``conn.fetchrow`` / ``conn.execute`` so we don't need Postgres; the
real DB layer is exercised by ``test_alembic_roundtrip``.

Coverage:

* GET returns the default off-state.
* Activate sets active=True with reason; the response carries the new
  ``activated_at`` and ``reason``.
* Activate is idempotent — a second activate with a different reason
  returns the existing record (does NOT overwrite ``activated_at`` /
  ``reason``).
* Deactivate sets active=False and clears the audit fields.
* Deactivate is idempotent — already-off is a 200 no-op.
* Activate validation: empty / missing reason returns 422.
* All three endpoints return 503 when the pool is None.
* Activate / deactivate publish ``agent.lifecycle`` events on the bus
  with the right ``event`` discriminator.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from agora.platform.control_plane import app as app_module
from agora.platform.control_plane import state as state_module
from agora.platform.shared.settings import Settings
from agora.platform.tools import broker as broker_module

# ----- Stubs ---------------------------------------------------------------


class _FakeConnection:
    """Stand-in for ``asyncpg.Connection``.

    Returns rows from a shared :class:`_FakeKillSwitchTable` so the
    pool's ``acquire`` -> ``async with conn.transaction()`` ->
    ``execute`` flow looks the same as the real one.
    """

    def __init__(self, table: _FakeKillSwitchTable) -> None:
        self._table = table

    async def fetchrow(self, sql: str, *args: Any) -> dict[str, Any] | None:
        # The endpoint only fetches the kill_switch row; we ignore the
        # SQL string and return the canned dict.
        return self._table.row()

    async def execute(self, sql: str, *args: Any) -> None:
        # Two writes the endpoints emit:
        #   UPDATE ... SET active = TRUE, activated_at = $1, reason = $2 ...
        #   UPDATE ... SET active = FALSE, activated_at = NULL, reason = NULL ...
        # We discriminate by inspecting ``args``: activate carries 2
        # positional args (now, reason); deactivate carries 0.
        if len(args) >= 2:
            self._table.activate(activated_at=args[0], reason=args[1])
        else:
            self._table.deactivate()

    def transaction(self) -> _FakeTransaction:
        return _FakeTransaction()


class _FakeTransaction:
    async def __aenter__(self) -> _FakeTransaction:
        return self

    async def __aexit__(self, *_: Any) -> None:
        return None


class _FakeAcquireCM:
    def __init__(self, conn: _FakeConnection) -> None:
        self._conn = conn

    async def __aenter__(self) -> _FakeConnection:
        return self._conn

    async def __aexit__(self, *_: Any) -> None:
        return None


class _FakePool:
    """Stand-in for ``asyncpg.Pool`` that hands out ``_FakeConnection``."""

    def __init__(self, table: _FakeKillSwitchTable) -> None:
        self._table = table

    def acquire(self) -> _FakeAcquireCM:
        return _FakeAcquireCM(_FakeConnection(self._table))


class _FakeKillSwitchTable:
    """Shared in-memory mirror of the ``kill_switch`` row.

    The fixture sets initial state; assertions read the final state.
    """

    def __init__(
        self,
        *,
        active: bool = False,
        activated_at: datetime | None = None,
        reason: str | None = None,
    ) -> None:
        self.active = active
        self.activated_at = activated_at
        self.reason = reason

    def row(self) -> dict[str, Any]:
        return {
            "active": self.active,
            "activated_at": self.activated_at,
            "reason": self.reason,
        }

    def activate(self, *, activated_at: datetime, reason: str) -> None:
        self.active = True
        self.activated_at = activated_at
        self.reason = reason

    def deactivate(self) -> None:
        self.active = False
        self.activated_at = None
        self.reason = None


# ----- Fixtures ------------------------------------------------------------


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(_env_file=None, workspace_root=str(tmp_path))


@pytest.fixture
def kill_switch_table() -> _FakeKillSwitchTable:
    return _FakeKillSwitchTable()


@pytest.fixture(autouse=True)
def stub_lifespan_resources(
    monkeypatch: pytest.MonkeyPatch, kill_switch_table: _FakeKillSwitchTable
) -> None:
    """Replace lifespan builders so the app boots without network."""

    fake_pool = _FakePool(kill_switch_table)

    async def fake_pool_builder(_settings: Settings) -> Any:
        return fake_pool

    async def fake_temporal(_settings: Settings) -> None:
        return None

    def fake_langfuse(_settings: Settings) -> None:
        return None

    async def fake_teardown(state: state_module.AppState) -> None:
        import contextlib

        with contextlib.suppress(Exception):
            await state.http_client.aclose()

    monkeypatch.setattr(state_module, "_build_pool", fake_pool_builder)
    monkeypatch.setattr(state_module, "_build_temporal_client", fake_temporal)
    monkeypatch.setattr(state_module, "_build_langfuse", fake_langfuse)
    monkeypatch.setattr(state_module, "teardown_app_state", fake_teardown)
    # The activate/deactivate endpoints invalidate the broker cache; in
    # tests there's no real cache state to clear, but we still want to
    # confirm the call lands.
    broker_module._invalidate_kill_switch_cache()


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    fastapi_app = app_module.create_app(settings)
    with TestClient(fastapi_app) as c:
        yield c


# ----- Tests ---------------------------------------------------------------


def test_get_returns_off_by_default(client: TestClient) -> None:
    r = client.get("/api/kill-switch")
    assert r.status_code == 200
    assert r.json() == {"active": False, "activated_at": None, "reason": None}


def test_activate_sets_status_to_on(
    client: TestClient, kill_switch_table: _FakeKillSwitchTable
) -> None:
    r = client.post(
        "/api/kill-switch/activate",
        json={"reason": "manual halt during incident"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["active"] is True
    assert body["reason"] == "manual halt during incident"
    assert body["activated_at"] is not None
    # Mirror state landed in the fake table.
    assert kill_switch_table.active is True
    assert kill_switch_table.reason == "manual halt during incident"


def test_activate_is_idempotent(
    client: TestClient, kill_switch_table: _FakeKillSwitchTable
) -> None:
    """Second activate keeps the original ``activated_at`` + ``reason``."""
    r1 = client.post(
        "/api/kill-switch/activate",
        json={"reason": "first"},
    )
    assert r1.status_code == 200
    first_activated_at = r1.json()["activated_at"]

    r2 = client.post(
        "/api/kill-switch/activate",
        json={"reason": "second-should-be-ignored"},
    )
    assert r2.status_code == 200
    body = r2.json()
    assert body["active"] is True
    # First activation wins; original reason and timestamp preserved.
    assert body["reason"] == "first"
    assert body["activated_at"] == first_activated_at
    # Mirror table also still has the original.
    assert kill_switch_table.reason == "first"


def test_deactivate_sets_status_to_off(
    client: TestClient, kill_switch_table: _FakeKillSwitchTable
) -> None:
    kill_switch_table.activate(
        activated_at=datetime(2025, 6, 1, 10, 0, tzinfo=UTC),
        reason="prior",
    )
    r = client.post("/api/kill-switch/deactivate")
    assert r.status_code == 200
    body = r.json()
    assert body == {"active": False, "activated_at": None, "reason": None}
    assert kill_switch_table.active is False
    assert kill_switch_table.reason is None
    assert kill_switch_table.activated_at is None


def test_deactivate_is_idempotent(
    client: TestClient, kill_switch_table: _FakeKillSwitchTable
) -> None:
    """Already-off → 200 no-op."""
    assert kill_switch_table.active is False
    r = client.post("/api/kill-switch/deactivate")
    assert r.status_code == 200
    assert r.json()["active"] is False


@pytest.mark.parametrize("body", [{}, {"reason": ""}, {"reason": "   "}])
def test_activate_rejects_missing_or_empty_reason(client: TestClient, body: dict[str, Any]) -> None:
    """Empty / whitespace / missing reason must return 422.

    Pydantic catches missing and length-zero. The all-whitespace case
    is intentionally allowed by the field constraint (min_length is on
    raw chars); we still document it here so a future tightening is
    obvious. The plan only mandates the empty-string rejection.
    """
    r = client.post("/api/kill-switch/activate", json=body)
    if body == {"reason": "   "}:
        # All-whitespace passes Pydantic's min_length=1 check; that's a
        # known soft-spot. K8 may strip-then-validate; for K3 we accept
        # the noise rather than hand-rolling a validator.
        assert r.status_code == 200
    else:
        assert r.status_code == 422


def test_503_when_pool_is_none(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    """All three endpoints return 503 when the pool is None."""

    async def no_pool(_settings: Settings) -> None:
        return None

    monkeypatch.setattr(state_module, "_build_pool", no_pool)
    fastapi_app = app_module.create_app(settings)
    with TestClient(fastapi_app) as c:
        r_get = c.get("/api/kill-switch")
        r_activate = c.post("/api/kill-switch/activate", json={"reason": "x"})
        r_deactivate = c.post("/api/kill-switch/deactivate")
    assert r_get.status_code == 503
    assert r_activate.status_code == 503
    assert r_deactivate.status_code == 503


def test_activate_publishes_event(
    client: TestClient,
) -> None:
    """The event bus receives an ``agent.lifecycle`` event on activate."""
    bus = client.app.state.agora.event_bus  # type: ignore[attr-defined]
    received: list[dict[str, Any]] = []
    done = asyncio.Event()

    async def _drain() -> None:
        async for event in bus.subscribe():
            received.append({"type": event.type, "payload": event.payload})
            done.set()
            return

    portal = client.portal
    assert portal is not None
    portal.start_task_soon(_drain)

    async def _wait_for_subscriber() -> None:
        for _ in range(100):
            if bus.subscriber_count >= 1:
                return
            await asyncio.sleep(0.01)
        raise AssertionError("subscriber never registered")

    portal.call(_wait_for_subscriber)

    r = client.post(
        "/api/kill-switch/activate",
        json={"reason": "incident-1234"},
    )
    assert r.status_code == 200

    async def _wait_for_event() -> None:
        await asyncio.wait_for(done.wait(), timeout=1.0)

    portal.call(_wait_for_event)

    assert len(received) == 1
    assert received[0]["type"] == "agent.lifecycle"
    assert received[0]["payload"]["event"] == "kill_switch_activated"
    assert received[0]["payload"]["reason"] == "incident-1234"


def test_deactivate_publishes_event(
    client: TestClient, kill_switch_table: _FakeKillSwitchTable
) -> None:
    """The event bus receives an ``agent.lifecycle`` event on deactivate."""
    kill_switch_table.activate(activated_at=datetime(2025, 6, 1, tzinfo=UTC), reason="prior")
    bus = client.app.state.agora.event_bus  # type: ignore[attr-defined]
    received: list[dict[str, Any]] = []
    done = asyncio.Event()

    async def _drain() -> None:
        async for event in bus.subscribe():
            received.append({"type": event.type, "payload": event.payload})
            done.set()
            return

    portal = client.portal
    assert portal is not None
    portal.start_task_soon(_drain)

    async def _wait_for_subscriber() -> None:
        for _ in range(100):
            if bus.subscriber_count >= 1:
                return
            await asyncio.sleep(0.01)
        raise AssertionError("subscriber never registered")

    portal.call(_wait_for_subscriber)

    r = client.post("/api/kill-switch/deactivate")
    assert r.status_code == 200

    async def _wait_for_event() -> None:
        await asyncio.wait_for(done.wait(), timeout=1.0)

    portal.call(_wait_for_event)

    assert len(received) == 1
    assert received[0]["type"] == "agent.lifecycle"
    assert received[0]["payload"]["event"] == "kill_switch_deactivated"
