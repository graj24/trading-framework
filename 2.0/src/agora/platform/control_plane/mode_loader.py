"""Load active mode overrides from Postgres.

The ``mode_overrides`` table (migration ``0001_initial``) is the source of
truth for manual mode overrides — per the framework rule "one source of
truth per state". The /api/mode endpoint must read it; computing mode from
the clock alone, ignoring overrides, is drift.

This module is intentionally thin: a single SELECT, mapped to the
``Override`` dataclass that ``mode.compute_mode`` already accepts. The
controller stays pure (no DB import).
"""

from __future__ import annotations

from datetime import datetime

import asyncpg
from loguru import logger

from agora.platform.control_plane.mode import Mode, Override

_SELECT_ACTIVE = """
    SELECT id, requested_at, mode, expires_at
    FROM mode_overrides
    WHERE expires_at > $1
    ORDER BY requested_at
"""


async def load_active_overrides(
    pool: asyncpg.Pool | None,
    now: datetime,
) -> list[Override]:
    """Return all unexpired overrides at ``now``.

    If the pool is unavailable or the query raises, log and return [] —
    the endpoint falls through to the clock-driven mode rather than
    crashing the request. This matches the K1 contract for /api/pms.
    """
    if pool is None:
        logger.warning("load_active_overrides: postgres pool unavailable; returning []")
        return []
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(_SELECT_ACTIVE, now)
    except Exception as e:
        logger.warning(
            "load_active_overrides: SELECT failed ({err}); returning []",
            err=f"{type(e).__name__}: {e}",
        )
        return []
    return [
        Override(
            mode=_coerce_mode(r["mode"]),
            expires_at=r["expires_at"],
            requested_at=r["requested_at"],
        )
        for r in rows
    ]


def _coerce_mode(raw: str) -> Mode:
    """Validate the mode string read from Postgres.

    The migration does not constrain the column to an enum — yet — so an
    operator could insert garbage. Treat unknown values as ``build`` and
    log loudly: the safest fallback when the override table is corrupt.
    """
    if raw in ("build", "trading", "pre_trade_freeze"):
        return raw  # type: ignore[return-value]
    logger.warning("mode_overrides has unknown mode={!r}; coercing to 'build'", raw)
    return "build"


__all__ = ["load_active_overrides"]
