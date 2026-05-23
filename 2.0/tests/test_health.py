"""FastAPI control-plane tests.

The ping helpers and lifespan resource builders are monkey-patched so the
endpoint tests don't require any real services. The aggregation logic
(worst-of) is the only non-trivial bit on the API side; we cover its three
states. We also assert the lifespan actually builds (and tears down) an
``AppState`` — the load-bearing fix from the K1 audit.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from agora.platform.control_plane import app as app_module
from agora.platform.control_plane import health
from agora.platform.control_plane import state as state_module
from agora.platform.shared.settings import Settings


@pytest.fixture
def settings() -> Settings:
    return Settings(_env_file=None)


@pytest.fixture(autouse=True)
def stub_lifespan_resources(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace the resource builders so the lifespan never touches the network.

    The test app still goes through the real lifespan path — that's the
    point — but each builder returns a sentinel so we don't try to dial
    Postgres / Temporal / Langfuse from a unit test.
    """

    async def fake_pool(settings: Settings) -> object:
        return object()

    async def fake_temporal(settings: Settings) -> object:
        return object()

    def fake_langfuse(settings: Settings) -> None:
        return None

    async def fake_teardown(state: state_module.AppState) -> None:
        # http_client is a real httpx.AsyncClient; close it like prod would.
        with contextlib.suppress(Exception):
            await state.http_client.aclose()

    monkeypatch.setattr(state_module, "_build_pool", fake_pool)
    monkeypatch.setattr(state_module, "_build_temporal_client", fake_temporal)
    monkeypatch.setattr(state_module, "_build_langfuse", fake_langfuse)
    monkeypatch.setattr(state_module, "teardown_app_state", fake_teardown)


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    fastapi_app = app_module.create_app(settings)
    # Using TestClient as a context manager triggers the lifespan, which is
    # what we want — endpoints depend on app.state.agora being populated.
    with TestClient(fastapi_app) as c:
        yield c


def _stub_pings(monkeypatch: pytest.MonkeyPatch, **statuses: tuple[str, str]) -> None:
    """Replace each ping_* with an async stub returning the given (status, detail)."""

    def make(result: tuple[str, str]) -> Any:
        async def _stub(*args: Any, **kwargs: Any) -> tuple[str, str]:
            return result

        return _stub

    monkeypatch.setattr(health, "ping_postgres", make(statuses.get("postgres", ("ok", "stubbed"))))
    monkeypatch.setattr(health, "ping_temporal", make(statuses.get("temporal", ("ok", "stubbed"))))
    monkeypatch.setattr(health, "ping_langfuse", make(statuses.get("langfuse", ("ok", "stubbed"))))
    monkeypatch.setattr(health, "ping_letta", make(statuses.get("letta", ("ok", "stubbed"))))
    monkeypatch.setattr(health, "ping_qdrant", make(statuses.get("qdrant", ("ok", "stubbed"))))


def test_root(client: TestClient) -> None:
    r = client.get("/")
    assert r.status_code == 200
    assert r.json()["service"] == "agora-control-plane"


def test_lifespan_populates_app_state(settings: Settings) -> None:
    """The lifespan must attach an AppState with our singletons to app.state."""
    fastapi_app = app_module.create_app(settings)
    with TestClient(fastapi_app):
        state = fastapi_app.state.agora
        assert isinstance(state, state_module.AppState)
        # http_client is built by build_app_state itself (not stubbed out).
        assert state.http_client is not None
        # The Postgres / Temporal / Langfuse builders are stubbed; pool and
        # client should be sentinels (truthy), langfuse should be None.
        assert state.postgres_pool is not None
        assert state.temporal_client is not None
        assert state.langfuse is None


def test_health_all_ok(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_pings(monkeypatch)
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert set(body["services"]) == {"postgres", "temporal", "langfuse", "letta", "qdrant"}
    assert all(svc["status"] == "ok" for svc in body["services"].values())


def test_health_degraded_when_langfuse_missing(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_pings(monkeypatch, langfuse=("degraded", "no keys"))
    r = client.get("/api/health")
    # Important: degraded still returns 200 so monitoring distinguishes
    # "API up, langfuse sad" from "API dead".
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "degraded"
    assert body["services"]["langfuse"]["status"] == "degraded"


def test_health_down_dominates_degraded(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_pings(
        monkeypatch,
        langfuse=("degraded", "no keys"),
        postgres=("down", "ECONNREFUSED"),
    )
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "down"


def test_request_id_echoed(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_pings(monkeypatch)
    r = client.get("/api/health", headers={"X-Request-ID": "test-req-123"})
    assert r.headers.get("X-Request-ID") == "test-req-123"


def test_request_id_generated_when_absent(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_pings(monkeypatch)
    r = client.get("/api/health")
    rid = r.headers.get("X-Request-ID")
    assert rid is not None and len(rid) >= 32  # uuid4 hex is 32 chars sans dashes


def test_pms_empty_when_pool_unavailable(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the lifespan couldn't build a pool, /api/pms returns []."""

    async def no_pool(settings: Settings) -> None:
        return None

    monkeypatch.setattr(state_module, "_build_pool", no_pool)
    fastapi_app = app_module.create_app(settings)
    with TestClient(fastapi_app) as c:
        r = c.get("/api/pms")
    assert r.status_code == 200
    assert r.json() == []


def test_mode_returns_shape(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    r = client.get("/api/mode")
    assert r.status_code == 200
    body = r.json()
    assert body["mode"] in ("build", "trading", "pre_trade_freeze")
    assert "as_of" in body
    assert "next_transition" in body
