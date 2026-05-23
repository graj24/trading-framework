"""WorkflowEnvironment tests for the PMSupervisor.

The real activities touch Postgres and the filesystem; for these tests we
replace each one with a mock under the same ``@activity.defn(name=...)``
key so the workflow dispatches to the mock. Temporal looks activities up
by name, so the workflow code is tested verbatim.

We use ``WorkflowEnvironment.start_time_skipping()`` so ``workflow.sleep``
is virtual — the 60-second cycle does not block the test for 60 seconds.
If the test server binary is unavailable in the local sandbox, the test
self-skips; ``make ci-local`` keeps green and the integration suite
(``make test-all``) surfaces the missing dependency.

Two cases:
  - test_supervisor_runs_one_cycle_then_stops: the loop runs one
    full cycle of get_mode + heartbeat, then a stop signal exits it.
  - test_supervisor_pause_skips_heartbeats_then_resume: pause early,
    confirm no heartbeats happen for a while, resume, confirm they
    resume, then stop.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from datetime import timedelta
from typing import Any

import pytest
from temporalio import activity
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from agora.platform.workers.pm_supervisor import (
    HeartbeatInput,
    PMConfig,
    PMSupervisor,
    ProvisionInput,
    ProvisionResult,
)

pytestmark = pytest.mark.integration


async def _start_env() -> WorkflowEnvironment:
    """Best-effort env start, preferring time-skipping."""
    try:
        return await WorkflowEnvironment.start_time_skipping()
    except Exception:
        return await WorkflowEnvironment.start_local()


def _make_mock_activities(
    calls: list[tuple[str, Any]],
    *,
    mode: str = "build",
) -> list[Any]:
    """Build a fresh set of in-memory replacements for the workflow's activities.

    Each one records ``(name, payload)`` into the shared ``calls`` list so
    tests can assert on the call sequence.
    """

    @activity.defn(name="provision_pm_workspace")
    async def mock_provision(payload: ProvisionInput) -> ProvisionResult:
        calls.append(("provision", payload.pm_id))
        return ProvisionResult(
            workspace_path=f"/tmp/{payload.pm_id}",
            build_cycle_seconds=5,
            trading_cycle_seconds=5,
        )

    @activity.defn(name="mark_pm_running")
    async def mock_running(pm_id: str) -> None:
        calls.append(("running", pm_id))

    @activity.defn(name="mark_pm_stopped")
    async def mock_stopped(pm_id: str) -> None:
        calls.append(("stopped", pm_id))

    @activity.defn(name="get_current_mode")
    async def mock_get_mode(pm_id: str) -> str:
        calls.append(("get_mode", pm_id))
        return mode

    @activity.defn(name="heartbeat_journal")
    async def mock_heartbeat(payload: HeartbeatInput) -> None:
        calls.append(("heartbeat", payload.mode))

    return [
        mock_provision,
        mock_running,
        mock_stopped,
        mock_get_mode,
        mock_heartbeat,
    ]


async def test_supervisor_runs_one_cycle_then_stops() -> None:
    """Provision -> running -> get_mode -> heartbeat -> stop -> stopped."""
    try:
        env = await _start_env()
    except Exception as e:
        pytest.skip(f"Temporal test server unavailable: {e}")

    calls: list[tuple[str, Any]] = []
    activities = _make_mock_activities(calls, mode="build")
    task_queue = f"test-supervisor-{uuid.uuid4()}"

    async with (
        env,
        Worker(
            env.client,
            task_queue=task_queue,
            workflows=[PMSupervisor],
            activities=activities,
        ),
    ):
        # Time-skipping cycles: a small build_cycle_seconds keeps the
        # virtual clock close to the activities. The actual value is
        # irrelevant when the env skips time.
        config = PMConfig(
            pm_id="pm1",
            name="PM1",
            starting_capital_inr=1_000_000.0,
            build_cycle_seconds=5,
            trading_cycle_seconds=5,
        )
        handle = await env.client.start_workflow(
            PMSupervisor.run,
            config,
            id=f"pm-test-{uuid.uuid4()}",
            task_queue=task_queue,
        )

        # Wait until at least one heartbeat lands, then signal stop.
        # We poll calls because the workflow is happening on the
        # in-process worker concurrently. Time-skipping means the
        # post-heartbeat sleep advances quickly.
        for _ in range(50):
            if any(c[0] == "heartbeat" for c in calls):
                break
            await asyncio.sleep(0.05)
        else:
            pytest.fail(f"no heartbeat observed within budget; calls={calls}")

        await handle.signal(PMSupervisor.stop)
        await handle.result()

    # Assert call shape: provision and running first; stopped last.
    names = [c[0] for c in calls]
    assert names[0] == "provision"
    assert names[1] == "running"
    assert names[-1] == "stopped"
    # At least one cycle's worth of get_mode + heartbeat.
    assert ("get_mode", "pm1") in calls
    assert ("heartbeat", "build") in calls


async def test_supervisor_pause_skips_heartbeats_then_resumes() -> None:
    """Pause stops heartbeats from accruing; resume restarts them."""
    try:
        env = await _start_env()
    except Exception as e:
        pytest.skip(f"Temporal test server unavailable: {e}")

    calls: list[tuple[str, Any]] = []
    activities = _make_mock_activities(calls, mode="build")
    task_queue = f"test-supervisor-{uuid.uuid4()}"

    async with (
        env,
        Worker(
            env.client,
            task_queue=task_queue,
            workflows=[PMSupervisor],
            activities=activities,
        ),
    ):
        config = PMConfig(
            pm_id="pm1",
            name="PM1",
            starting_capital_inr=1_000_000.0,
            build_cycle_seconds=60,
            trading_cycle_seconds=60,
            paused_poll_seconds=1,
        )
        handle = await env.client.start_workflow(
            PMSupervisor.run,
            config,
            id=f"pm-test-{uuid.uuid4()}",
            task_queue=task_queue,
        )

        # Wait (in virtual time) until the workflow has booted so the
        # 'running' marker is visible. Auto-time-skipping advances the
        # env clock past workflow.sleep boundaries, so this is fast.
        for _ in range(50):
            if any(c[0] == "running" for c in calls):
                break
            await asyncio.sleep(0.05)
        else:
            pytest.fail(f"workflow never reported running; calls={calls}")

        # Pause before any heartbeats accrue. Race-tolerant: if a few
        # have already landed, that's fine; we measure deltas.
        await handle.signal(PMSupervisor.pause)

        # Let the env advance virtual time well past 5 build cycles.
        # While paused, NO new heartbeats should accumulate even though
        # the virtual clock advances minutes.
        await env.sleep(timedelta(seconds=5 * 60))
        hb_paused = sum(1 for c in calls if c[0] == "heartbeat")

        # Advance again — confirm count is still flat under pause.
        await env.sleep(timedelta(seconds=5 * 60))
        hb_paused_2 = sum(1 for c in calls if c[0] == "heartbeat")
        assert (
            hb_paused_2 == hb_paused
        ), f"pause must freeze heartbeats; before={hb_paused} after={hb_paused_2}"

        # Resume and let one full cycle elapse — at least one new heartbeat.
        await handle.signal(PMSupervisor.resume)
        await env.sleep(timedelta(seconds=2 * 60))

        hb_after_resume = sum(1 for c in calls if c[0] == "heartbeat")
        assert hb_after_resume > hb_paused_2, (
            f"resume must restart heartbeats; paused={hb_paused_2} after={hb_after_resume} "
            f"calls={calls}"
        )

        await handle.signal(PMSupervisor.stop)
        with contextlib.suppress(Exception):
            await handle.result()

    # Sanity: the stopped marker ran on graceful exit.
    assert any(c[0] == "stopped" for c in calls), calls
