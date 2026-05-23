"""FastAPI control-plane tests.

The ping helpers are monkey-patched so the endpoint test doesn't require any
real services. The aggregation logic (worst-of) is the only non-trivial bit on
the API side; we cover its three states.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from agora.platform.control_plane import app as app_module
from agora.platform.control_plane import health
from agora.platform.shared.settings import Settings


@pytest.fixture
def settings() -> Settings:
    return Settings(_env_file=None)


@pytest.fixture
def client(settings: Settings) -> TestClient:
    fastapi_app = app_module.create_app(settings)
    return TestClient(fastapi_app)


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


def test_pms_empty(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    # Force the asyncpg call path to fail so the endpoint returns []. This
    # avoids the need for a live Postgres in the unit-test environment.
    import asyncpg

    async def _boom(*args: Any, **kwargs: Any) -> Any:
        raise OSError("no postgres in test env")

    monkeypatch.setattr(asyncpg, "connect", _boom)
    r = client.get("/api/pms")
    assert r.status_code == 200
    assert r.json() == []


def test_mode_returns_shape(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    r = client.get("/api/mode")
    assert r.status_code == 200
    body = r.json()
    assert body["mode"] in ("build", "trading", "pre_trade_freeze")
    assert "as_of" in body
    assert "next_transition" in body
