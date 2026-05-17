"""Lightweight observability helpers — wraps Agent.run with timing logs.

Usage in a sub-class:

    from core.timing import timed_run

    class FooAgent(Agent):
        @timed_run
        def run(self, context=None) -> AgentResult: ...

The decorator emits a single INFO log line on every call:

    timing | agent=Foo symbol=RELIANCE duration_ms=1234 status=DONE

Failures (returns AgentResult with status=ERROR, or raises) are logged at
WARNING with the same shape so downstream log aggregators can compute SLOs.

Intentionally tiny — no Prometheus / OpenTelemetry deps. The real
observability story is on the P2 roadmap (06-improvements.md §C6).
"""
from __future__ import annotations

import functools
import logging
import time
from typing import Any, Callable

logger = logging.getLogger("trading.timing")


def timed_run(func: Callable[..., Any]) -> Callable[..., Any]:
    """Decorator: log wall time of an Agent.run call."""

    @functools.wraps(func)
    def wrapper(self, context: dict | None = None) -> Any:
        agent_name = getattr(self, "name", type(self).__name__)
        symbol = (context or {}).get("symbol", "-")
        t0 = time.perf_counter()
        status = "ERROR"
        try:
            result = func(self, context)
            try:
                status = result.status.value if hasattr(result, "status") else "DONE"
            except Exception:
                status = "DONE"
            return result
        finally:
            elapsed_ms = round((time.perf_counter() - t0) * 1000, 1)
            level = logging.INFO if status == "done" else logging.WARNING
            logger.log(
                level,
                "timing | agent=%s symbol=%s duration_ms=%s status=%s",
                agent_name, symbol, elapsed_ms, status,
            )

    return wrapper
