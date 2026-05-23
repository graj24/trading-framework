"""Process-lifetime asyncpg pool for worker activities.

Activities run inside the Temporal worker process, which has no FastAPI
lifespan to manage long-lived resources. Each activity invocation
opening a fresh asyncpg connection would be wasteful and floods Postgres
under any real cadence; we want one pool per worker process, built
lazily on first use, closed on worker shutdown.

This module is **never** imported from a workflow module — only from
activity bodies (which run outside the workflow sandbox). That's why
the ``import asyncpg`` at module top is safe: the workflow validator
re-imports the workflow module under a strict sandbox, and that import
graph stops at ``temporalio`` + stdlib.
"""

from __future__ import annotations

import asyncio

import asyncpg
from loguru import logger

_pool: asyncpg.Pool | None = None
_pool_lock = asyncio.Lock()


async def get_or_build_pool() -> asyncpg.Pool:
    """Return the worker-process-wide asyncpg pool, building it on first call.

    Mirrors the lifespan pool's URL handling (``+asyncpg`` -> bare scheme)
    and pool sizing (a worker is a fan-out point, so the upper bound is
    a touch lower than the API's). Concurrent first-callers race on
    ``_pool_lock``; only one builds.
    """
    global _pool
    if _pool is not None:
        return _pool
    async with _pool_lock:
        if _pool is not None:
            return _pool
        from agora.platform.shared.settings import get_settings

        settings = get_settings()
        bare_url = settings.postgres_url.replace("postgresql+asyncpg://", "postgresql://", 1)
        logger.info("worker: building asyncpg pool")
        _pool = await asyncpg.create_pool(bare_url, min_size=1, max_size=5)
    return _pool


async def close_pool() -> None:
    """Close the worker-process pool. Idempotent; safe to call from shutdown."""
    global _pool
    if _pool is None:
        return
    pool = _pool
    _pool = None
    try:
        await pool.close()
    except Exception as e:
        logger.warning("worker: closing pool raised: {}", e)


__all__ = ["close_pool", "get_or_build_pool"]
