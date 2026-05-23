"""Workflow + activity definitions for the K1 end-to-end test.

This module is deliberately minimal — it contains ONLY Temporal decorators
and is free of any imports that the Temporal workflow sandbox forbids
(sqlalchemy, testcontainers, asyncpg, pytest, etc.). The Temporal worker
re-imports the workflow's module under a strict sandbox during validation;
keeping this file's surface area tiny is what makes that validation pass.

The activity body itself defers the AgoraLLM / litellm imports until call
time, which is fine because activities run outside the sandbox.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from temporalio import activity, workflow


@activity.defn(name="record_llm_call")
async def record_llm_call(pm_id: str) -> str:
    """Activity that performs one AgoraLLM call against a stubbed completion.

    The AgoraLLM import is deferred to here (not the module top) so the
    workflow sandbox does not see litellm/langfuse during validation.
    """
    from agora.platform.llm.client import AgoraLLM

    async def fake_completion(**_: Any) -> dict[str, Any]:
        return {
            "choices": [{"message": {"content": "hello, integration"}}],
            "usage": {"prompt_tokens": 7, "completion_tokens": 3},
        }

    llm = AgoraLLM(
        agent_id="e2e-activity",
        pm_id=pm_id,
        completion_fn=fake_completion,
        cost_fn=lambda usage, model: 0.0042,
    )
    result = await llm.call(
        model="anthropic/claude-sonnet-4-5",
        messages=[{"role": "user", "content": "hi"}],
        task_id="e2e-task-1",
    )
    return result.content


@workflow.defn(name="HelloWithBudget")
class HelloWithBudgetWorkflow:
    @workflow.run
    async def run(self, pm_id: str) -> str:
        return await workflow.execute_activity(
            record_llm_call,
            pm_id,
            start_to_close_timeout=timedelta(seconds=30),
        )
