"""Process-lifetime httpx client for worker activities.

Mirrors ``workers/_pool.py``: one client per worker process, lazily
built on first use, closed on shutdown. The activity bodies that
POST to the API (heartbeat publisher, future K3+ activities) all
share this. Like _pool.py, this module is imported only from
activity bodies (never from the workflow module top), so the
import-time httpx footprint is OK here.
"""

from __future__ import annotations

import asyncio
import contextlib

import httpx

_client: httpx.AsyncClient | None = None
_lock = asyncio.Lock()


async def get_or_build_http_client() -> httpx.AsyncClient:
    """Return the worker-process-wide httpx client, building on first call."""
    global _client
    if _client is not None:
        return _client
    async with _lock:
        if _client is not None:
            return _client
        _client = httpx.AsyncClient(timeout=2.0)
    return _client


async def close_http_client() -> None:
    """Close the worker-process httpx client. Idempotent."""
    global _client
    if _client is None:
        return
    client = _client
    _client = None
    with contextlib.suppress(Exception):
        await client.aclose()


__all__ = ["close_http_client", "get_or_build_http_client"]
