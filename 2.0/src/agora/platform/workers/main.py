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
from agora.platform.workers import _http, _pool
from agora.platform.workers.hello import HelloWorkflow, say_hello
from agora.platform.workers.pm_supervisor import (
    EodCloser,
    PMSupervisor,
    eod_close_activity,
    get_current_mode,
    heartbeat_journal,
    list_running_pms_activity,
    mark_pm_running,
    mark_pm_stopped,
    provision_pm_workspace,
    trading_cycle_activity,
)

DEFAULT_TASK_QUEUE = "agora"

# K3 Step 3.6 — EOD closer schedule. NSE closes at 15:30 IST (10:00 UTC)
# Mon-Fri; the closer fires five minutes before close so the trading
# cycle has stopped placing fresh orders. The schedule id is stable so
# repeated worker restarts ``ScheduleAlreadyRunningError`` instead of
# clobbering — we treat that as success.
EOD_SCHEDULE_ID = "eod-closer"
EOD_HOUR_UTC = 9
EOD_MINUTE_UTC = 55


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
        workflows=[HelloWorkflow, PMSupervisor, EodCloser],
        activities=[
            say_hello,
            mark_pm_running,
            mark_pm_stopped,
            provision_pm_workspace,
            get_current_mode,
            heartbeat_journal,
            trading_cycle_activity,
            eod_close_activity,
            list_running_pms_activity,
        ],
    )

    # K3 Step 3.6: register the daily EOD closer schedule. Best-effort —
    # if Temporal hiccups here we still want the worker to start so it
    # can drain its task queue. ``ScheduleAlreadyRunningError`` is the
    # happy path on every subsequent worker boot.
    await _ensure_eod_schedule(client, task_queue)

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
        # Same pattern for the process-lifetime httpx client used by
        # heartbeat (and future K3+ activity HTTP calls).
        await _http.close_http_client()
        logger.info("worker stopped")


async def _ensure_eod_schedule(client: Client, task_queue: str) -> None:
    """Create the ``EodCloser`` Temporal schedule (idempotent).

    Fires Mon-Fri at 09:55 UTC (= 15:25 IST, 5 minutes before NSE
    close). On every worker restart we re-issue ``create_schedule``;
    ``ScheduleAlreadyRunningError`` is the expected path and not a
    failure. Other RPC errors are logged, not raised, so a Temporal
    blip at startup never blocks the worker from coming up to drain
    the task queue.

    Calendar spec uses ``ScheduleRange(0, 4)`` for ``day_of_week``
    (Mon-Fri; Temporal's calendar spec is 0-Sunday in some places
    and 0-Monday in others — Temporal Python uses 0=Sunday matching
    Go-style cron, so 1-5 is Mon-Fri). NSE has no Saturday/Sunday
    sessions; holiday handling is not encoded here — on a holiday
    the closer runs and finds zero open trades because the trading
    cycle never opened any (mode controller blocks during freeze).
    """
    from temporalio.client import (
        Schedule,
        ScheduleActionStartWorkflow,
        ScheduleAlreadyRunningError,
        ScheduleCalendarSpec,
        ScheduleRange,
        ScheduleSpec,
    )

    spec = ScheduleSpec(
        calendars=[
            ScheduleCalendarSpec(
                # Mon=1 .. Fri=5 in Temporal Python's 0-Sunday model.
                day_of_week=[ScheduleRange(1, 5)],
                hour=[ScheduleRange(EOD_HOUR_UTC)],
                minute=[ScheduleRange(EOD_MINUTE_UTC)],
            )
        ],
    )
    schedule = Schedule(
        action=ScheduleActionStartWorkflow(
            EodCloser.run,
            id="eod-closer-{ScheduledStartTime}",
            task_queue=task_queue,
        ),
        spec=spec,
    )
    try:
        await client.create_schedule(EOD_SCHEDULE_ID, schedule)
        logger.info(
            "registered EOD schedule {} (Mon-Fri {:02d}:{:02d} UTC)",
            EOD_SCHEDULE_ID,
            EOD_HOUR_UTC,
            EOD_MINUTE_UTC,
        )
    except ScheduleAlreadyRunningError:
        logger.debug("EOD schedule {} already exists; skipping create", EOD_SCHEDULE_ID)
    except Exception as e:  # defensive — never block worker startup
        logger.warning("failed to register EOD schedule: {}", e)


def main_sync(task_queue: str = DEFAULT_TASK_QUEUE) -> None:
    """Sync wrapper for tyro / console-script entry."""
    asyncio.run(main(task_queue))


if __name__ == "__main__":
    tyro.cli(main_sync)
