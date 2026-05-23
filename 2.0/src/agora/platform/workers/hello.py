"""Hello-world workflow + activity.

Smallest possible Temporal pair so we can prove the worker registers, the
activity executes, and the workflow surfaces in the Temporal UI. This is K1
plumbing only — real workflows live in K2+.
"""

from __future__ import annotations

from datetime import timedelta

from temporalio import activity, workflow


@activity.defn
async def say_hello(name: str) -> str:
    return f"hello, {name}"


@workflow.defn
class HelloWorkflow:
    @workflow.run
    async def run(self, name: str) -> str:
        return await workflow.execute_activity(
            say_hello,
            name,
            start_to_close_timeout=timedelta(seconds=5),
        )


__all__ = ["HelloWorkflow", "say_hello"]
