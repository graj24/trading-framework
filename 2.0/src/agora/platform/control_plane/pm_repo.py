"""Asyncpg-backed repository for the ``pms`` table.

Owns the typed Pydantic models and CRUD helpers used by the FastAPI
control plane (and, in K2 Step 2.2, by the PMSupervisor activities).

Why a thin module instead of SQLAlchemy ORM?
  * The control plane already talks asyncpg directly (see ``app.py``,
    ``health.py``, ``mode_loader.py``); pulling SQLAlchemy in for one
    table would mean two DB drivers, two connection-pool stories, and
    two error-handling shapes for the same shared pool.
  * The schema is small and the queries are direct. The cost of an ORM
    here is debugging machinery, not earned abstraction.

Status vocabulary is fixed (``Status``). Anything outside the literal
set is a bug — callers must coerce/validate before insert.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

import asyncpg
from pydantic import BaseModel

# Fixed PM lifecycle vocabulary. Matches plan/01-KEYSTONE.md §4.
Status = Literal["provisioning", "spawned", "running", "paused", "stopped", "error"]


class PMSummary(BaseModel):
    """Public, list-friendly view of a PM. Used by ``GET /api/pms``."""

    id: str
    name: str
    status: str


class PMRecord(BaseModel):
    """Full row from the ``pms`` table. Used by ``GET /api/pms/{pm_id}``.

    ``starting_capital_inr`` round-trips as ``float`` for JSON ergonomics;
    the column is ``NUMERIC`` in Postgres so we cast at the boundary.
    """

    id: str
    name: str
    status: str
    starting_capital_inr: float
    spawned_at: datetime
    stopped_at: datetime | None
    prompt_path: str
    config: dict[str, Any]
    workflow_id: str | None


_INSERT_SQL = """
    INSERT INTO pms (id, name, status, starting_capital_inr, prompt_path, config)
    VALUES ($1, $2, 'provisioning', $3, $4, $5::jsonb)
"""

_SELECT_ONE_SQL = """
    SELECT id, name, status, starting_capital_inr, spawned_at, stopped_at,
           prompt_path, config, workflow_id
    FROM pms
    WHERE id = $1
"""

_SELECT_LIST_SQL = "SELECT id, name, status FROM pms ORDER BY spawned_at"

_EXISTS_SQL = "SELECT 1 FROM pms WHERE id = $1 LIMIT 1"

_UPDATE_STATUS_SQL = "UPDATE pms SET status = $2 WHERE id = $1"

_UPDATE_WORKFLOW_ID_SQL = "UPDATE pms SET workflow_id = $2 WHERE id = $1"


def _row_to_record(row: asyncpg.Record) -> PMRecord:
    """Translate an asyncpg row into ``PMRecord``.

    asyncpg returns ``NUMERIC`` as ``Decimal`` and ``JSONB`` as a Python
    primitive (asyncpg has the json codec registered by default for any
    server-side jsonb column). We coerce both at the boundary so the
    rest of the app sees plain ``float`` / ``dict``.
    """
    raw_capital = row["starting_capital_inr"]
    capital = float(raw_capital) if isinstance(raw_capital, Decimal) else float(raw_capital)
    raw_config = row["config"]
    if isinstance(raw_config, str):
        # Some asyncpg setups return JSONB as text if no codec is set.
        import json

        config = json.loads(raw_config)
    else:
        config = dict(raw_config) if raw_config is not None else {}
    return PMRecord(
        id=row["id"],
        name=row["name"],
        status=row["status"],
        starting_capital_inr=capital,
        spawned_at=row["spawned_at"],
        stopped_at=row["stopped_at"],
        prompt_path=row["prompt_path"],
        config=config,
        workflow_id=row["workflow_id"],
    )


async def insert_pm(
    pool: asyncpg.Pool,
    *,
    pm_id: str,
    name: str,
    starting_capital_inr: float,
    prompt_path: str,
    config: dict[str, Any],
) -> None:
    """Insert a new PM row in status ``provisioning``.

    Raises ``asyncpg.UniqueViolationError`` if ``pm_id`` already exists —
    callers that want a 409 should check ``pm_exists`` first; the unique
    constraint is the safety net, not the primary check.
    """
    import json

    async with pool.acquire() as conn:
        await conn.execute(
            _INSERT_SQL,
            pm_id,
            name,
            starting_capital_inr,
            prompt_path,
            json.dumps(config),
        )


async def update_pm_status(pool: asyncpg.Pool, pm_id: str, status: Status) -> None:
    """Set ``pms.status``. No-op if ``pm_id`` does not exist."""
    async with pool.acquire() as conn:
        await conn.execute(_UPDATE_STATUS_SQL, pm_id, status)


async def update_pm_workflow_id(
    pool: asyncpg.Pool,
    pm_id: str,
    workflow_id: str | None,
) -> None:
    """Set ``pms.workflow_id``. Used by Step 2.2 once the workflow starts."""
    async with pool.acquire() as conn:
        await conn.execute(_UPDATE_WORKFLOW_ID_SQL, pm_id, workflow_id)


async def get_pm(pool: asyncpg.Pool, pm_id: str) -> PMRecord | None:
    """Fetch the full PM record. Returns ``None`` when missing."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(_SELECT_ONE_SQL, pm_id)
    if row is None:
        return None
    return _row_to_record(row)


async def list_pms(pool: asyncpg.Pool) -> list[PMSummary]:
    """List all PMs in spawn order."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(_SELECT_LIST_SQL)
    return [PMSummary(id=r["id"], name=r["name"], status=r["status"]) for r in rows]


async def pm_exists(pool: asyncpg.Pool, pm_id: str) -> bool:
    """Return True iff a row with this ``pm_id`` exists."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(_EXISTS_SQL, pm_id)
    return row is not None


__all__ = [
    "PMRecord",
    "PMSummary",
    "Status",
    "get_pm",
    "insert_pm",
    "list_pms",
    "pm_exists",
    "update_pm_status",
    "update_pm_workflow_id",
]
