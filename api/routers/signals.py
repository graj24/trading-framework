from __future__ import annotations
import asyncio
from fastapi import APIRouter, BackgroundTasks
from api.schemas.signal import SignalScores
from core.knowledge_base import read_kb

router = APIRouter(prefix="/api/signals", tags=["signals"])

# In-memory cache of last signal run per symbol
_signal_cache: dict[str, dict] = {}


@router.get("/{symbol}", response_model=SignalScores)
def get_signal(symbol: str):
    symbol = symbol.upper()
    cached = _signal_cache.get(symbol)
    if cached:
        return cached

    # Return KB data as a best-effort signal summary
    fund = read_kb(symbol, "fundamentals.json")
    weights = read_kb(symbol, "signal_weights.json")
    return SignalScores(
        symbol=symbol,
        price=fund.get("current_price"),
        technical_score=weights.get("technical", {}).get("weight"),
        sentiment=weights.get("news", {}).get("weight"),
        pattern_ev=None,
        regime=None,
        decision=None,
        raw=weights,
    )


def _run_analysis(symbol: str):
    """Background task: run MasterAgent for a symbol and cache result."""
    try:
        from agents.master import MasterAgent
        from core.config import get_config
        agent = MasterAgent(get_config())
        result = agent.run({"symbol": symbol})
        data = result.data or {}
        _signal_cache[symbol] = {
            "symbol": symbol,
            "price": data.get("price"),
            "technical_score": data.get("technical_score"),
            "sentiment": data.get("sentiment"),
            "pattern_ev": data.get("pattern_ev"),
            "ml_signal": data.get("ml_signal"),
            "ml_proba": data.get("ml_proba"),
            "regime": data.get("regime"),
            "decision": data.get("decision"),
            "confidence": data.get("confidence"),
            "reasoning": data.get("reasoning"),
            "raw": data,
        }
    except Exception as e:
        _signal_cache[symbol] = {"symbol": symbol, "decision": "ERROR", "reasoning": str(e)}


@router.post("/run")
def run_signal(symbol: str, background_tasks: BackgroundTasks):
    symbol = symbol.upper()
    background_tasks.add_task(_run_analysis, symbol)
    return {"status": "queued", "symbol": symbol}
