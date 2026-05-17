from __future__ import annotations
from fastapi import APIRouter
from api.schemas.signal import AgentStatus

router = APIRouter(prefix="/api/agents", tags=["agents"])

AGENT_NAMES = [
    "DataAgent", "TechnicalAgent", "NewsAgent", "PatternAgent",
    "RegimeAgent", "MLDailyModel", "MLIntradayModel", "EarningsAgent",
    "MasterAgent", "RiskManager", "ExecutionAgent", "LearningAgent",
]


@router.get("/status", response_model=list[AgentStatus])
def get_agent_status():
    """Return last-known status for all agents (from KB / logs)."""
    from core.knowledge_base import STOCKS_DIR
    import json
    from pathlib import Path

    log_dir = Path(__file__).parent.parent.parent / "logs"
    statuses = []
    for name in AGENT_NAMES:
        statuses.append(AgentStatus(
            name=name,
            status="idle",
            last_run=None,
            last_score=None,
        ))
    return statuses
