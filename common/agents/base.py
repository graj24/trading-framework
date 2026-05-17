"""Base agent abstract class for all trading agents."""
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Optional


class AgentStatus(Enum):
    IDLE = "idle"
    RUNNING = "running"
    DONE = "done"
    ERROR = "error"


@dataclass
class AgentResult:
    agent_name: str
    status: AgentStatus
    data: dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    timestamp: datetime = field(default_factory=datetime.now)

    def ok(self) -> bool:
        return self.status == AgentStatus.DONE


class Agent(ABC):
    """Abstract base for all trading agents.

    B.3 / C6: every concrete subclass automatically gets per-agent timing
    via the `core.timing.timed_run` decorator wrapped around its `run`
    method. This is opt-out (set `_TIMED_RUN = False` on a subclass to
    skip), but every agent in this repo benefits by default.
    """

    _TIMED_RUN: bool = True

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        # Only wrap if the subclass actually defines `run` itself, AND it
        # hasn't already been wrapped (idempotent so re-importing or
        # multiple-inheritance doesn't double-wrap).
        if "run" in cls.__dict__ and getattr(cls, "_TIMED_RUN", True):
            from core.timing import timed_run
            run_method = cls.__dict__["run"]
            if not getattr(run_method, "_timed", False):
                wrapped = timed_run(run_method)
                wrapped._timed = True  # type: ignore[attr-defined]
                cls.run = wrapped

    def __init__(self, name: str, config: dict):
        self.name = name
        self.config = config
        self._status = AgentStatus.IDLE

    @abstractmethod
    def run(self, context: Optional[dict] = None) -> AgentResult:
        """Execute the agent's main task. Returns AgentResult."""

    def status(self) -> AgentStatus:
        return self._status

    def report(self) -> dict:
        return {"agent": self.name, "status": self._status.value}

    def _result(self, data: dict) -> AgentResult:
        self._status = AgentStatus.DONE
        return AgentResult(agent_name=self.name, status=AgentStatus.DONE, data=data)

    def _error(self, msg: str) -> AgentResult:
        self._status = AgentStatus.ERROR
        return AgentResult(agent_name=self.name, status=AgentStatus.ERROR, error=msg)
