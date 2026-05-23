"""Integration test for ``WS /api/stream``.

We boot the FastAPI app with the standard test stubs (no Postgres / no
Temporal), open a WebSocket via ``TestClient.websocket_connect``, then
publish a synthetic event to the live ``state.event_bus``. The client
must receive that event with the correct shape.

Latency note: the test publishes from a background asyncio task after
the WS handshake has registered the subscriber. Without this two-step,
the publish races the subscribe and the event would be dropped (no
replay).
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
def settings(tmp_path: Path) -> Settings:
    return Settings(_env_file=None, workspace_root=str(tmp_path))


@pytest.fixture(autouse=True)
def stub_lifespan_resources(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace lifespan builders so the app boots without network.

    Same pattern as test_pms_endpoints; duplicated here so this module
    can run in isolation without importing private fixtures.
    """

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


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    fastapi_app = app_module.create_app(settings)
    with TestClient(fastapi_app) as c:
        yield c


def test_ws_stream_receives_published_event(client: TestClient) -> None:
    """Subscribe via WS, publish via state.event_bus, receive via WS."""
    bus = client.app.state.agora.event_bus  # type: ignore[attr-defined]
    portal = client.portal  # starlette anyio portal — runs coros on the app loop
    assert portal is not None  # guaranteed inside `with TestClient(...) as c`

    with client.websocket_connect("/api/stream") as ws:
        # Wait until the subscribe() async iterator has registered its
        # queue with the bus before publishing — otherwise the event is
        # broadcast to zero subscribers and dropped.
        async def _wait_for_subscriber() -> None:
            for _ in range(100):
                if bus.subscriber_count >= 1:
                    return
                await asyncio.sleep(0.01)
            raise AssertionError("ws never registered with the bus")

        portal.call(_wait_for_subscriber)

        # Publish on the app's event loop so the queue.put_nowait lands
        # on the same loop the WS subscriber is reading from.
        portal.call(
            bus.publish,
            "pm.heartbeat",
            {"pm_id": "pm1", "mode": "build"},
        )

        msg = ws.receive_json()
        assert msg["type"] == "pm.heartbeat"
        assert msg["payload"] == {"pm_id": "pm1", "mode": "build"}
        assert isinstance(msg["ts"], str) and msg["ts"]
