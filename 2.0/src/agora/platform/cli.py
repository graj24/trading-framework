"""`agora-cli` — small operator CLI built on tyro.

Subcommands:
  hello <name>   — start a HelloWorkflow on the `agora` task queue, await,
                   and print the result. Used by `make hello-smoke`.
  worker         — run the Temporal worker. Equivalent to
                   `python -m agora.platform.workers.main`.

The CLI is intentionally tiny in K1; K2+ will add subcommands for PM
lifecycle, eval runs, etc.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Annotated

import tyro
from temporalio.client import Client

from agora.platform.shared.settings import get_settings
from agora.platform.workers.hello import HelloWorkflow
from agora.platform.workers.main import DEFAULT_TASK_QUEUE
from agora.platform.workers.main import main_sync as worker_main


async def _run_hello(name: str, task_queue: str) -> str:
    settings = get_settings()
    client = await Client.connect(
        settings.temporal_host,
        namespace=settings.temporal_namespace,
    )
    workflow_id = f"hello-{uuid.uuid4()}"
    return await client.execute_workflow(
        HelloWorkflow.run,
        name,
        id=workflow_id,
        task_queue=task_queue,
    )


def hello(
    name: Annotated[str, tyro.conf.Positional],
    task_queue: str = DEFAULT_TASK_QUEUE,
) -> None:
    """Trigger HelloWorkflow with `name` and print the result.

    Args:
        name: Name to greet.
        task_queue: Task queue the worker is listening on.
    """
    result = asyncio.run(_run_hello(name, task_queue))
    print(result)


def worker(task_queue: str = DEFAULT_TASK_QUEUE) -> None:
    """Run the Temporal worker on the given task queue."""
    worker_main(task_queue)


def main() -> None:
    """`agora-cli` console-script entry point."""
    # Late-bind the subcommand callables so tests that monkeypatch
    # `cli.hello` / `cli.worker` see the patched versions.
    import sys

    this_module = sys.modules[__name__]
    tyro.extras.subcommand_cli_from_dict(
        {
            "hello": this_module.hello,
            "worker": this_module.worker,
        }
    )


if __name__ == "__main__":
    main()
