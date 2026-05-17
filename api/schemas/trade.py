from __future__ import annotations
from typing import Optional, Union
from pydantic import BaseModel


class Trade(BaseModel):
    id: Union[int, str]
    symbol: str
    entry_date: Optional[str] = None
    entry_price: Optional[float] = None
    stop_loss: Optional[float] = None
    target: Optional[float] = None
    position_size: Optional[int] = None
    exit_date: Optional[str] = None
    exit_price: Optional[float] = None
    pnl_pct: Optional[float] = None
    pnl_inr: Optional[float] = None
    outcome: Optional[str] = None
    reasoning: Optional[str] = None
    created_at: Optional[str] = None
    technical_score: Optional[float] = None
    sentiment: Optional[float] = None
    pattern_ev: Optional[float] = None
    sector_momentum: Optional[float] = None
    regime_alignment: Optional[float] = None
    signal_source: Optional[str] = None
