"""Temporal worker entry point.

Connects to the Temporal cluster configured in `Settings`, registers the K1
hello-world pair, and runs the worker until SIGINT/SIGTERM. The CLI wrapper at
the bottom (`tyro.cli`) is what `make worker` and `agora-cli worker` invoke.
"""

from __future__ import annotations

import asyncio
import contextlib
import signal

import tyro
from loguru import logger
from temporalio.client import Client
from temporalio.worker import Worker

from agora.platform.observability.logging import configure_logging
from agora.platform.shared.settings import get_settings
from agora.platform.workers import _pool
from agora.platform.workers.hello import HelloWorkflow, say_hello
from agora.platform.workers.pm_supervisor import (
    PMSupervisor,
    get_current_mode,
    heartbeat_journal,
    mark_pm_running,
    mark_pm_stopped,
    provision_pm_workspace,
)

DEFAULT_TASK_QUEUE = "agora"


async def main(task_queue: str = DEFAULT_TASK_QUEUE) -> None:
    """Run the AGORA Temporal worker until cancelled.

    Args:
        task_queue: Temporal task queue to poll. K1 uses a single queue
            (`agora`) for everything; later keystones partition by domain.
    """
    settings = get_settings()
    configure_logging(settings.log_format)

    client = await Client.connect(
        settings.temporal_host,
        namespace=settings.temporal_namespace,
    )
    worker = Worker(
        client,
        task_queue=task_queue,
        workflows=[HelloWorkflow, PMSupervisor],
        activities=[
            say_hello,
            mark_pm_running,
            mark_pm_stopped,
            provision_pm_workspace,
            get_current_mode,
            heartbeat_journal,
        ],
    )

    # Cancel the worker.run() task on SIGINT/SIGTERM. Worker.run() treats
    # cancellation as a graceful shutdown signal.
    loop = asyncio.get_running_loop()
    run_task = asyncio.create_task(worker.run())

    def _request_stop() -> None:
        if not run_task.done():
            logger.info("worker stop requested")
            run_task.cancel()

    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            # Windows / restricted event loops — fall back to default handling.
            loop.add_signal_handler(sig, _request_stop)

    logger.info("worker starting on task_queue={}", task_queue)
    try:
        await run_task
    except asyncio.CancelledError:
        # Expected on SIGINT/SIGTERM after our handler cancels run_task.
        pass
    finally:
        # Best-effort: close the worker-process asyncpg pool that
        # activities lazily built. Failures are logged inside.
        await _pool.close_pool()
        logger.info("worker stopped")


def main_sync(task_queue: str = DEFAULT_TASK_QUEUE) -> None:
    """Sync wrapper for tyro / console-script entry."""
    asyncio.run(main(task_queue))


if __name__ == "__main__":
    tyro.cli(main_sync)
