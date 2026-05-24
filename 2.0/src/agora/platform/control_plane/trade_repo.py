"""Asyncpg-backed repository for the ``paper_trades`` table.

K3 Step 3.4. Mirrors the shape of :mod:`agora.platform.control_plane.pm_repo`
— typed Pydantic records, raw asyncpg queries, no ORM. The broker tool
writes here on submission; the dashboard reads from here for the PM
positions card. The leaderboard query in K7 is a ``SUM(pnl_inr)``
``WHERE outcome != 'open'`` over this table.

Outcome vocabulary is fixed (:data:`TradeOutcome`). Anything outside
the literal set is a bug — callers must coerce/validate before insert.
"""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

import asyncpg
from pydantic import BaseModel, ConfigDict

#: Trade lifecycle. ``open`` is the only non-terminal state — the
#: broker writes that on submit, the EOD closer / SL handler / manual
#: action / strategy signal-reversal moves it to one of the five
#: terminal states. The column is plain TEXT in Postgres (see
#: migration 0003); the literal here is the only enforcement, which
#: lets the vocabulary grow without a schema migration. ``signal_exit``
#: was added in the K3 post-audit pass to give strategy-driven closes
#: their own bucket; conflating them with operator ``manual`` closes
#: confuses K4+ PM exit-reason analytics. K8 hardening can layer a
#: CHECK constraint once the set is frozen.
TradeOutcome = Literal["open", "sl_hit", "target_hit", "eod_close", "manual", "signal_exit"]

#: Order side. K3 strategy is long-only; ``SHORT`` is included so the
#: schema doesn't need a follow-up migration when K4+ strategies trade
#: short.
TradeSide = Literal["LONG", "SHORT"]


class PaperTradeRecord(BaseModel):
    """Full row from the ``paper_trades`` table.

    ``Decimal`` round-trips through asyncpg's NUMERIC codec without
    precision loss; the dashboard JSON layer converts to string at the
    serialization boundary.
    """

    # ``Decimal`` is not JSON-native but pydantic will encode it; allow
    # arbitrary types so we don't need to write custom validators.
    model_config = ConfigDict(arbitrary_types_allowed=True)

    id: int
    pm_id: str
    symbol: str
    side: TradeSide
    quantity: int
    entry_price: Decimal | None
    entry_ts: datetime | None
    stop_loss: Decimal | None
    target: Decimal | None
    exit_price: Decimal | None
    exit_ts: datetime | None
    outcome: TradeOutcome
    pnl_inr: Decimal | None
    pnl_pct: Decimal | None
    strategy_id: str | None
    metadata: dict[str, Any]


_INSERT_OPEN_SQL = """
    INSERT INTO paper_trades (
        pm_id, symbol, side, quantity,
        entry_price, entry_ts,
        stop_loss, target,
        outcome, strategy_id, metadata
    ) VALUES (
        $1, $2, $3, $4,
        $5, $6,
        $7, $8,
        'open', $9, $10::jsonb
    )
    RETURNING id
"""

_CLOSE_SQL = """
    UPDATE paper_trades
    SET exit_price = $2,
        exit_ts    = $3,
        outcome    = $4,
        pnl_inr    = $5,
        pnl_pct    = $6
    WHERE id = $1 AND outcome = 'open'
    RETURNING id
"""

_SELECT_BY_ID_SQL = """
    SELECT id, pm_id, symbol, side, quantity, entry_price, entry_ts,
           stop_loss, target, exit_price, exit_ts, outcome,
           pnl_inr, pnl_pct, strategy_id, metadata
    FROM paper_trades
    WHERE id = $1
"""

_LIST_OPEN_SQL = """
    SELECT id, pm_id, symbol, side, quantity, entry_price, entry_ts,
           stop_loss, target, exit_price, exit_ts, outcome,
           pnl_inr, pnl_pct, strategy_id, metadata
    FROM paper_trades
    WHERE pm_id = $1 AND outcome = 'open'
    ORDER BY entry_ts NULLS LAST, id
"""

_LIST_ALL_SQL = """
    SELECT id, pm_id, symbol, side, quantity, entry_price, entry_ts,
           stop_loss, target, exit_price, exit_ts, outcome,
           pnl_inr, pnl_pct, strategy_id, metadata
    FROM paper_trades
    WHERE pm_id = $1
    ORDER BY id DESC
    LIMIT $2
"""


def _row_to_record(row: asyncpg.Record) -> PaperTradeRecord:
    """Translate an asyncpg row into a :class:`PaperTradeRecord`.

    asyncpg returns NUMERIC as ``Decimal`` and JSONB as a Python dict
    (codec is registered by default for jsonb columns in the asyncpg
    versions we pin). We keep ``Decimal`` end-to-end so PnL math has
    no float drift.
    """
    raw_metadata = row["metadata"]
    if isinstance(raw_metadata, str):
        # Some asyncpg setups return JSONB as text if no codec is set.
        metadata = json.loads(raw_metadata)
    else:
        metadata = dict(raw_metadata) if raw_metadata is not None else {}
    return PaperTradeRecord(
        id=row["id"],
        pm_id=row["pm_id"],
        symbol=row["symbol"],
        side=row["side"],
        quantity=row["quantity"],
        entry_price=row["entry_price"],
        entry_ts=row["entry_ts"],
        stop_loss=row["stop_loss"],
        target=row["target"],
        exit_price=row["exit_price"],
        exit_ts=row["exit_ts"],
        outcome=row["outcome"],
        pnl_inr=row["pnl_inr"],
        pnl_pct=row["pnl_pct"],
        strategy_id=row["strategy_id"],
        metadata=metadata,
    )


async def insert_open_trade(
    pool: asyncpg.Pool,
    *,
    pm_id: str,
    symbol: str,
    side: TradeSide,
    quantity: int,
    entry_price: Decimal,
    entry_ts: datetime,
    stop_loss: Decimal | None = None,
    target: Decimal | None = None,
    strategy_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> int:
    """Insert a new open trade. Returns the new row id.

    The ``outcome`` is hard-coded to ``'open'`` server-side — callers
    must use :func:`close_trade` to transition out of the open state.
    """
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            _INSERT_OPEN_SQL,
            pm_id,
            symbol,
            side,
            quantity,
            entry_price,
            entry_ts,
            stop_loss,
            target,
            strategy_id,
            json.dumps(metadata or {}),
        )
    assert row is not None  # RETURNING id always yields a row on insert
    return int(row["id"])


async def close_trade(
    pool: asyncpg.Pool,
    trade_id: int,
    *,
    exit_price: Decimal,
    exit_ts: datetime,
    outcome: TradeOutcome,
) -> PaperTradeRecord:
    """Update an open trade with exit info and return the closed record.

    Computes ``pnl_inr`` and ``pnl_pct`` from the existing entry_price
    and quantity:

    * ``LONG``:  ``pnl_inr = (exit - entry) * qty``
    * ``SHORT``: ``pnl_inr = (entry - exit) * qty``

    ``pnl_pct = pnl_inr / (entry * qty) * 100``.

    Raises ``ValueError`` if the trade is missing, already closed, or
    the requested ``outcome`` is the non-terminal ``'open'``.
    """
    if outcome == "open":
        raise ValueError("close_trade requires a terminal outcome, got 'open'")
    async with pool.acquire() as conn, conn.transaction():
        existing = await conn.fetchrow(_SELECT_BY_ID_SQL, trade_id)
        if existing is None:
            raise ValueError(f"trade {trade_id} not found")
        if existing["outcome"] != "open":
            raise ValueError(
                f"trade {trade_id} is already closed " f"(outcome={existing['outcome']!r})"
            )

        entry_price: Decimal = existing["entry_price"]
        quantity: int = int(existing["quantity"])
        side: str = existing["side"]
        if side == "LONG":
            pnl_inr = (exit_price - entry_price) * Decimal(quantity)
        else:
            pnl_inr = (entry_price - exit_price) * Decimal(quantity)
        cost_basis = entry_price * Decimal(quantity)
        pnl_pct = (pnl_inr / cost_basis) * Decimal(100) if cost_basis != 0 else Decimal(0)

        await conn.execute(
            _CLOSE_SQL,
            trade_id,
            exit_price,
            exit_ts,
            outcome,
            pnl_inr,
            pnl_pct,
        )
        row = await conn.fetchrow(_SELECT_BY_ID_SQL, trade_id)
    assert row is not None
    return _row_to_record(row)


async def get_trade(pool: asyncpg.Pool, trade_id: int) -> PaperTradeRecord | None:
    """Fetch one trade by id. Returns ``None`` when missing."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(_SELECT_BY_ID_SQL, trade_id)
    if row is None:
        return None
    return _row_to_record(row)


async def list_open_trades(pool: asyncpg.Pool, pm_id: str) -> list[PaperTradeRecord]:
    """List all currently open trades for a PM, oldest first."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(_LIST_OPEN_SQL, pm_id)
    return [_row_to_record(r) for r in rows]


async def list_trades(pool: asyncpg.Pool, pm_id: str, limit: int = 100) -> list[PaperTradeRecord]:
    """List the most recent ``limit`` trades for a PM (newest first)."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(_LIST_ALL_SQL, pm_id, limit)
    return [_row_to_record(r) for r in rows]


__all__ = [
    "PaperTradeRecord",
    "TradeOutcome",
    "TradeSide",
    "close_trade",
    "get_trade",
    "insert_open_trade",
    "list_open_trades",
    "list_trades",
]
