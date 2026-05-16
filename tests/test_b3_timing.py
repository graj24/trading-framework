"""Tests for B.3 / C6 — `core.timing.timed_run` decorator."""
from __future__ import annotations

import logging
import time

from agents.base import Agent, AgentResult, AgentStatus
from core.timing import timed_run


class _FastAgent(Agent):
    def __init__(self):
        super().__init__("FastAgent", {})

    @timed_run
    def run(self, context=None):  # type: ignore[override]
        time.sleep(0.01)
        return self._result({"ok": True})


def test_timed_run_logs_duration_and_status(caplog):
    agent = _FastAgent()
    with caplog.at_level(logging.INFO, logger="trading.timing"):
        result = agent.run({"symbol": "RELIANCE"})

    assert result.ok()
    log_text = " ".join(r.getMessage() for r in caplog.records
                        if r.name == "trading.timing")
    assert "agent=FastAgent" in log_text
    assert "symbol=RELIANCE" in log_text
    assert "duration_ms=" in log_text


def test_timed_run_logs_warning_on_error(caplog):
    class _ErrAgent(Agent):
        def __init__(self):
            super().__init__("ErrAgent", {})

        @timed_run
        def run(self, context=None):  # type: ignore[override]
            return self._error("boom")

    agent = _ErrAgent()
    with caplog.at_level(logging.WARNING, logger="trading.timing"):
        agent.run({"symbol": "TCS"})
    msgs = [r.getMessage() for r in caplog.records if r.name == "trading.timing"]
    assert any("status=ERROR" in m or "status=error" in m for m in msgs), msgs


def test_timed_run_handles_exception(caplog):
    class _RaiseAgent(Agent):
        def __init__(self):
            super().__init__("RaiseAgent", {})

        @timed_run
        def run(self, context=None):  # type: ignore[override]
            raise ValueError("boom")

    agent = _RaiseAgent()
    with caplog.at_level(logging.WARNING, logger="trading.timing"):
        try:
            agent.run({"symbol": "INFY"})
        except ValueError:
            pass
    msgs = [r.getMessage() for r in caplog.records if r.name == "trading.timing"]
    assert any("status=ERROR" in m for m in msgs), msgs
