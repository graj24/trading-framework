from __future__ import annotations
from typing import Optional, Any
from pydantic import BaseModel


class SignalScores(BaseModel):
    symbol: str
    price: Optional[float] = None
    technical_score: Optional[float] = None
    sentiment: Optional[float] = None
    pattern_ev: Optional[float] = None
    ml_signal: Optional[Any] = None
    ml_proba: Optional[Any] = None
    regime: Optional[str] = None
    decision: Optional[str] = None
    confidence: Optional[float] = None
    reasoning: Optional[str] = None
    raw: Optional[dict] = None


class AgentStatus(BaseModel):
    name: str
    status: str
    last_run: Optional[str] = None
    last_score: Optional[float] = None
    details: Optional[dict] = None
