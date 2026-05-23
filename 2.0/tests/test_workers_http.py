"""Unit tests for the process-lifetime httpx client helper.

Mirrors the pattern in ``workers/_pool.py``: one client per process,
lazily built, closed on shutdown. The audit (F1/B1) flagged that the
heartbeat publisher was creating a fresh ``httpx.AsyncClient`` per call,
which churns sockets under K3+ cadence; this helper fixes that.
"""

from __future__ import annotations

import httpx

from agora.platform.workers import _http


async def test_get_or_build_http_client_memoizes() -> None:
    """Two consecutive calls return the same client instance."""
    # Reset module state so this test is order-independent.
    await _http.close_http_client()
    try:
        first = await _http.get_or_build_http_client()
        second = await _http.get_or_build_http_client()
        assert first is second
        assert isinstance(first, httpx.AsyncClient)
    finally:
        await _http.close_http_client()


async def test_close_http_client_is_idempotent() -> None:
    """Closing twice (or before any build) must not raise."""
    await _http.close_http_client()
    await _http.close_http_client()  # second close on already-closed state


async def test_close_then_rebuild_returns_fresh_client() -> None:
    """After close, the next ``get_or_build`` builds a new client."""
    await _http.close_http_client()
    try:
        first = await _http.get_or_build_http_client()
        await _http.close_http_client()
        second = await _http.get_or_build_http_client()
        assert first is not second
    finally:
        await _http.close_http_client()
