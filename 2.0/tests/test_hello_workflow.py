"""Hello-world workflow test using an in-process Temporal environment.

We use ``WorkflowEnvironment.start_time_skipping()`` when available because
it spins up a lightweight test server that supports time-skipping (faster
and required for any future workflows that sleep). If the test server binary
is not available in this environment (e.g. CI sandboxes that block the
download), we fall back to ``start_local()`` which uses an embedded dev
server. If neither is reachable, the test self-skips so default ``make
ci-local`` stays green; run with ``-m integration`` to surface the skip.
"""

from __future__ import annotations

import pytest
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from agora.platform.workers.hello import HelloWorkflow, say_hello


async def _start_env() -> WorkflowEnvironment:
    """Best-effort env start, preferring time-skipping."""
    try:
        return await WorkflowEnvironment.start_time_skipping()
    except Exception:
        return await WorkflowEnvironment.start_local()


@pytest.mark.integration
async def test_hello_workflow_returns_greeting() -> None:
    try:
        env = await _start_env()
    except Exception as e:
        pytest.skip(f"Temporal test server unavailable: {e}")

    async with (
        env,
        Worker(
            env.client,
            task_queue="test-agora",
            workflows=[HelloWorkflow],
            activities=[say_hello],
        ),
    ):
        result = await env.client.execute_workflow(
            HelloWorkflow.run,
            "world",
            id="test-hello-1",
            task_queue="test-agora",
        )
        assert result == "hello, world"
