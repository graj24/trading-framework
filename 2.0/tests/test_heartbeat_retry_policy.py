"""Structural test for the heartbeat activity's retry policy.

Audit finding F5: heartbeats are best-effort signals, not guaranteed
delivery. Temporal's default retry policy would re-run a slow tick and
produce duplicate journal lines (the heartbeat activity uses
``open("a")`` — append-only, no idempotency key). The next cycle's
heartbeat (one cadence period later) is the right way to recover.

This test asserts the property structurally: it AST-parses the
PMSupervisor source and confirms the ``execute_activity`` call for
``heartbeat_journal`` is invoked with
``retry_policy=RetryPolicy(maximum_attempts=1)``. Behavioral coverage
(observe a duplicate-line attempt and assert it doesn't happen) would
require an integration test with a real Temporal env + a mock that
raises once and a multi-cycle wallclock budget; the brittle-formatting
risk of the structural form is accepted at K2 because the load-bearing
property is "no retries on the heartbeat activity", and that one bit
is exactly what the AST check verifies.
"""

from __future__ import annotations

import ast
import inspect

from agora.platform.workers import pm_supervisor


def _find_heartbeat_execute_activity_call() -> ast.Call:
    """Walk the workflow module AST and return the call node that
    invokes ``workflow.execute_activity(heartbeat_journal, ...)``.
    """
    source = inspect.getsource(pm_supervisor)
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        # Match `workflow.execute_activity(...)`.
        func = node.func
        is_execute_activity = (
            isinstance(func, ast.Attribute)
            and func.attr == "execute_activity"
            and isinstance(func.value, ast.Name)
            and func.value.id == "workflow"
        )
        if not is_execute_activity:
            continue
        # First positional arg is the activity reference.
        if not node.args:
            continue
        first = node.args[0]
        if isinstance(first, ast.Name) and first.id == "heartbeat_journal":
            return node
    raise AssertionError(
        "did not find execute_activity(heartbeat_journal, ...) call in pm_supervisor"
    )


def test_heartbeat_activity_has_no_retry() -> None:
    """retry_policy kwarg must be RetryPolicy(maximum_attempts=1)."""
    call = _find_heartbeat_execute_activity_call()

    retry_kw = next((kw for kw in call.keywords if kw.arg == "retry_policy"), None)
    assert retry_kw is not None, (
        "heartbeat_journal activity must be invoked with an explicit "
        "retry_policy=RetryPolicy(maximum_attempts=1); the default policy "
        "would retry on transient failures and produce duplicate journal lines."
    )

    value = retry_kw.value
    assert isinstance(
        value, ast.Call
    ), f"retry_policy must be a RetryPolicy(...) call, got {value!r}"
    assert (
        isinstance(value.func, ast.Name) and value.func.id == "RetryPolicy"
    ), f"retry_policy must construct RetryPolicy directly, got {ast.dump(value.func)}"

    max_attempts_kw = next((kw for kw in value.keywords if kw.arg == "maximum_attempts"), None)
    assert max_attempts_kw is not None, "RetryPolicy must set maximum_attempts"
    assert isinstance(
        max_attempts_kw.value, ast.Constant
    ), f"maximum_attempts must be a literal int, got {ast.dump(max_attempts_kw.value)}"
    max_attempts = max_attempts_kw.value.value
    assert isinstance(max_attempts, int) and not isinstance(
        max_attempts, bool
    ), f"maximum_attempts must be an int literal, got {type(max_attempts).__name__}"
    assert max_attempts == 1, f"heartbeats must not retry; maximum_attempts={max_attempts!r}"


def test_heartbeat_activity_has_thirty_second_timeout() -> None:
    """start_to_close_timeout must be >= 30s.

    The activity does a file write + an HTTP POST + (best-effort) DB
    writes. K2's original 10s gave only 3-4x headroom; a transient API
    slowdown could trip it. The bump goes hand-in-hand with the
    no-retry policy: if the activity fails, the next cycle's heartbeat
    handles recovery, so the timeout has to be generous enough that
    happy-path slowness doesn't trip it.
    """
    call = _find_heartbeat_execute_activity_call()

    timeout_kw = next((kw for kw in call.keywords if kw.arg == "start_to_close_timeout"), None)
    assert timeout_kw is not None, "start_to_close_timeout must be set"

    # ``timedelta(seconds=N)`` — pull the literal N back out.
    value = timeout_kw.value
    assert isinstance(value, ast.Call), f"timeout must be a timedelta(...) call, got {value!r}"
    seconds_kw = next((kw for kw in value.keywords if kw.arg == "seconds"), None)
    assert seconds_kw is not None and isinstance(seconds_kw.value, ast.Constant)
    seconds = seconds_kw.value.value
    assert isinstance(seconds, int | float) and not isinstance(
        seconds, bool
    ), f"timedelta(seconds=...) must be a numeric literal, got {type(seconds).__name__}"
    assert seconds >= 30, (
        f"start_to_close_timeout for heartbeat must be at least 30s "
        f"(got {seconds}s); see pm_supervisor.py comment."
    )
