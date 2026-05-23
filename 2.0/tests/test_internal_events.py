"""Tests for ``POST /api/internal/events``.

The route is the K2 worker → API publish hook (Option A in plan §4 Step
2.5). Three behaviors are load-bearing:

  1. Without a configured token, the route is disabled (503). Local-dev
     default, prevents accidental open relay.
  2. With a configured token, requests with the wrong / missing
     ``x-agora-token`` header are rejected (401).
  3. With the right token, the request is broadcast on the event bus.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from agora.platform.control_plane import app as app_module
from agora.platform.control_plane import state as state_module
from agora.platform.shared.settings import Settings


@pytest.fixture
def settings_with_token(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        workspace_root=str(tmp_path),
        internal_event_token="test-token-123",
    )


@pytest.fixture
def settings_without_token(tmp_path: Path) -> Settings:
    return Settings(_env_file=None, workspace_root=str(tmp_path))


@pytest.fixture(autouse=True)
def stub_lifespan_resources(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_pool(settings: Settings) -> Any:
        return None

    async def fake_temporal(settings: Settings) -> Any:
        return None

    def fake_langfuse(settings: Settings) -> None:
        return None

    async def fake_teardown(state: state_module.AppState) -> None:
        with contextlib.suppress(Exception):
            await state.http_client.aclose()

    monkeypatch.setattr(state_module, "_build_pool", fake_pool)
    monkeypatch.setattr(state_module, "_build_temporal_client", fake_temporal)
    monkeypatch.setattr(state_module, "_build_langfuse", fake_langfuse)
    monkeypatch.setattr(state_module, "teardown_app_state", fake_teardown)


def _make_client(settings: Settings) -> Iterator[TestClient]:
    fastapi_app = app_module.create_app(settings)
    with TestClient(fastapi_app) as c:
        yield c


@pytest.fixture
def client_with_token(settings_with_token: Settings) -> Iterator[TestClient]:
    yield from _make_client(settings_with_token)


@pytest.fixture
def client_without_token(settings_without_token: Settings) -> Iterator[TestClient]:
    yield from _make_client(settings_without_token)


def test_internal_event_disabled_when_no_token_configured(
    client_without_token: TestClient,
) -> None:
    """Empty configured token → route is disabled (503).

    Even a request that *carries* a token is refused, so an attacker
    can't silently rely on an empty default.
    """
    r = client_without_token.post(
        "/api/internal/events",
        json={"type": "pm.heartbeat", "payload": {"pm_id": "pm1"}},
        headers={"x-agora-token": "any"},
    )
    assert r.status_code == 503
    assert "internal events disabled" in r.json()["detail"].lower()


def test_internal_event_requires_token(client_with_token: TestClient) -> None:
    """Wrong token → 401."""
    r = client_with_token.post(
        "/api/internal/events",
        json={"type": "pm.heartbeat", "payload": {"pm_id": "pm1"}},
        headers={"x-agora-token": "wrong"},
    )
    assert r.status_code == 401
    # Missing header → 422 from FastAPI's dependency layer (Header(...)).
    r2 = client_with_token.post(
        "/api/internal/events",
        json={"type": "pm.heartbeat", "payload": {"pm_id": "pm1"}},
    )
    assert r2.status_code == 422


def test_internal_event_publishes_to_bus(client_with_token: TestClient) -> None:
    """Right token → event is broadcast on the bus."""
    bus = client_with_token.app.state.agora.event_bus  # type: ignore[attr-defined]
    portal = client_with_token.portal
    assert portal is not None

    received: list[dict[str, Any]] = []
    done = asyncio.Event()

    async def _drain_one() -> None:
        async for event in bus.subscribe():
            received.append({"type": event.type, "payload": event.payload, "ts": event.ts})
            done.set()
            return

    # Schedule the subscriber on the app loop; wait for it to register.
    portal.start_task_soon(_drain_one)

    async def _wait_for_subscriber() -> None:
        for _ in range(100):
            if bus.subscriber_count >= 1:
                return
            await asyncio.sleep(0.01)
        raise AssertionError("subscriber never registered")

    portal.call(_wait_for_subscriber)

    r = client_with_token.post(
        "/api/internal/events",
        json={"type": "pm.heartbeat", "payload": {"pm_id": "pm1", "mode": "build"}},
        headers={"x-agora-token": "test-token-123"},
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True}

    async def _wait_for_event() -> None:
        await asyncio.wait_for(done.wait(), timeout=1.0)

    portal.call(_wait_for_event)

    assert len(received) == 1
    assert received[0]["type"] == "pm.heartbeat"
    assert received[0]["payload"] == {"pm_id": "pm1", "mode": "build"}
