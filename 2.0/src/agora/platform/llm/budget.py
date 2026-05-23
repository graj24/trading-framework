"""Async ``record_budget_event`` — single-row INSERT into ``budget_events``.

We use raw SQL via SQLAlchemy ``text()`` because the rest of the codebase
isn't using ORM yet (alembic-defined tables, asyncpg-shaped queries in
control_plane/health.py). Engines are cached per-URL so repeated calls don't
churn connection pools.

The migration ``0001_initial`` declares ``budget_events.pm_id`` as nullable,
referencing ``pms.id``. Passing ``pm_id=None`` is therefore a valid INSERT,
but K1 step 1.7 specifies system-level calls (e.g. the smoke script) should
*skip* recording entirely — leaves the table to be exclusively per-PM
attribution. We honour that here by short-circuiting when ``pm_id is None``.
"""

from __future__ import annotations

import json
from typing import Any

from loguru import logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from agora.platform.shared.settings import Settings

# Engines are expensive to construct (DNS, pool init); cache by URL so repeated
# calls in tests / production share a pool. The cache lives for the process
# lifetime — no eviction needed at K1 scale.
_engine_cache: dict[str, AsyncEngine] = {}
_sessionmaker_cache: dict[str, async_sessionmaker[Any]] = {}


def _get_sessionmaker(url: str) -> async_sessionmaker[Any]:
    if url not in _sessionmaker_cache:
        engine = create_async_engine(url, future=True, pool_pre_ping=True)
        _engine_cache[url] = engine
        _sessionmaker_cache[url] = async_sessionmaker(engine, expire_on_commit=False)
    return _sessionmaker_cache[url]


_INSERT_SQL = text(
    """
    INSERT INTO budget_events (pm_id, kind, amount_usd, metadata)
    VALUES (:pm_id, :kind, :amount, CAST(:metadata AS JSONB))
    RETURNING id
    """
)


async def record_budget_event(
    pm_id: str | None,
    kind: str,
    amount_usd: float,
    metadata: dict[str, Any] | None = None,
    settings: Settings | None = None,
) -> int | None:
    """Insert one row into ``budget_events``.

    Returns the new row id, or ``None`` if ``pm_id`` is None (system-level
    call — silently skipped, debug-logged). Raises on DB errors so callers
    can decide what to do; ``AgoraLLM.call`` catches and warns.
    """
    if pm_id is None:
        logger.debug(
            "record_budget_event skipped: pm_id=None kind={kind} amount_usd={amount}",
            kind=kind,
            amount=amount_usd,
        )
        return None

    settings = settings or Settings()
    sessionmaker = _get_sessionmaker(settings.postgres_url)

    payload = json.dumps(metadata or {})
    async with sessionmaker() as session:
        result = await session.execute(
            _INSERT_SQL,
            {
                "pm_id": pm_id,
                "kind": kind,
                "amount": amount_usd,
                "metadata": payload,
            },
        )
        row_id = int(result.scalar_one())
        await session.commit()
    return row_id


__all__ = ["record_budget_event"]
