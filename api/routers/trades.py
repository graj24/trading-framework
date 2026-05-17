from __future__ import annotations
from typing import Optional
from fastapi import APIRouter, HTTPException, Query
from api.deps import get_db
from api.schemas.trade import Trade

router = APIRouter(prefix="/api/trades", tags=["trades"])


def _row_to_trade(row) -> dict:
    return dict(row)


@router.get("", response_model=list[Trade])
def list_trades(
    symbol: Optional[str] = Query(None),
    outcome: Optional[str] = Query(None),
    limit: int = Query(200, le=1000),
):
    conn = get_db()
    sql = "SELECT * FROM trades WHERE 1=1"
    params: list = []
    if symbol:
        sql += " AND symbol = ?"
        params.append(symbol.upper())
    if outcome:
        sql += " AND outcome = ?"
        params.append(outcome)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [_row_to_trade(r) for r in rows]


@router.get("/{trade_id}", response_model=Trade)
def get_trade(trade_id: str):
    conn = get_db()
    row = conn.execute("SELECT * FROM trades WHERE id = ?", (trade_id,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(404, "Trade not found")
    return _row_to_trade(row)
