"""PMSupervisor — the Temporal workflow that owns one PM's lifecycle.

K2 Step 2.2. The PM has no LLM brain yet; for K2 the supervisor's only
jobs are:

  1. On start, idempotently re-provision the workspace (Temporal replay
     can re-execute this; provisioning is `mkdir -p` + write-if-absent).
  2. Mark the PM ``running`` in Postgres on first start.
  3. Loop until ``stop`` is signalled: query mode, append a heartbeat
     line to today's journal, sleep one cadence period.
  4. On graceful exit, mark ``stopped``.
  5. Honour ``pause`` / ``resume`` signals between cycles.

Sandbox discipline
------------------
Temporal validates workflow definitions by re-importing this module
under a restricted sandbox that bans most non-stdlib imports
(``asyncpg``, ``sqlalchemy``, ``langfuse``, ``litellm``, anything that
touches the network or RNG at import time). Therefore the **module
top** here imports only:

  - ``__future__``, stdlib (``datetime``, ``dataclasses``)
  - ``temporalio.{activity, workflow}``

Activity bodies run **outside** the sandbox; they are free to import
asyncpg / file-system helpers / settings / repos. We push every such
import into the activity function body. The K1 lesson encoded in
``tests/_e2e_workflow_module.py`` applies here verbatim.

Determinism
-----------
Workflow code uses ``workflow.now()`` and ``workflow.sleep()`` only.
Direct ``time.time()``, ``asyncio.sleep()``, ``random``, DB calls, file
I/O, etc. would break replay. All of those live in activities.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from temporalio import activity, workflow

# ---------------------------------------------------------------- payloads
# Plain dataclasses cross the workflow/activity boundary as JSON. Keep them
# pure — no methods, no computed defaults that depend on the wall clock.


@dataclass
class PMConfig:
    """Top-level config passed to ``PMSupervisor.run`` at workflow start."""

    pm_id: str
    name: str
    starting_capital_inr: float
    build_cycle_seconds: int = 60
    trading_cycle_seconds: int = 60
    # How often the supervisor wakes up while paused to re-check the
    # ``_paused`` flag. Production keeps this loose to minimize history
    # events; tests override to a tighter value to keep the pause loop
    # responsive without a real-time pause.
    paused_poll_seconds: int = 10


@dataclass
class ProvisionInput:
    """Args for the provision activity. Mirrors ``provision_workspace``."""

    pm_id: str
    name: str
    starting_capital_inr: float


@dataclass
class HeartbeatInput:
    """Args for the heartbeat activity."""

    pm_id: str
    mode: str  # "build" | "trading" | "pre_trade_freeze"


# --------------------------------------------------------------- activities
# Activities run on the worker process, outside the workflow sandbox.
# ALL non-stdlib imports must be deferred to function bodies so the
# workflow validator (which re-imports this module) does not see them.


@activity.defn(name="provision_pm_workspace")
async def provision_pm_workspace(payload: ProvisionInput) -> str:
    """Idempotently (re-)create the PM's workspace tree. Returns the path.

    Runs on every workflow start (including replays). The provisioner
    is mkdir-p + write-if-absent under the hood, so repeats are safe.
    """
    from agora.platform.control_plane.pm_provision import provision_workspace

    pm_dir = await provision_workspace(
        pm_id=payload.pm_id,
        name=payload.name,
        starting_capital_inr=payload.starting_capital_inr,
    )
    return str(pm_dir)


@activity.defn(name="mark_pm_running")
async def mark_pm_running(pm_id: str) -> None:
    """Set the PM's status to ``running``."""
    from agora.platform.control_plane.pm_repo import update_pm_status
    from agora.platform.workers._pool import get_or_build_pool

    pool = await get_or_build_pool()
    await update_pm_status(pool, pm_id, "running")


@activity.defn(name="mark_pm_stopped")
async def mark_pm_stopped(pm_id: str) -> None:
    """Set the PM's status to ``stopped`` on graceful supervisor exit."""
    from agora.platform.control_plane.pm_repo import update_pm_status
    from agora.platform.workers._pool import get_or_build_pool

    pool = await get_or_build_pool()
    await update_pm_status(pool, pm_id, "stopped")


@activity.defn(name="get_current_mode")
async def get_current_mode(pm_id: str) -> str:
    """Return the platform's current mode string.

    Reads any active overrides from Postgres so manual mode changes are
    honoured. Falls back to clock+calendar if the DB is unreachable
    (matching ``mode_loader.load_active_overrides`` semantics).
    """
    from datetime import UTC, datetime

    from agora.platform.control_plane import mode as mode_module
    from agora.platform.control_plane.mode_loader import load_active_overrides
    from agora.platform.workers._pool import get_or_build_pool

    pool = await get_or_build_pool()
    now = datetime.now(UTC)
    overrides = await load_active_overrides(pool, now)
    return mode_module.compute_mode(now, overrides=overrides).mode


@activity.defn(name="heartbeat_journal")
async def heartbeat_journal(payload: HeartbeatInput) -> None:
    """Append ``[<iso ts>] [<mode>]: alive`` to today's journal file.

    Also fires a best-effort ``pm.heartbeat`` to the API process so the
    dashboard ticker shows it. The publish goes via the
    ``POST /api/internal/events`` HTTP hook (Option A in plan §4 Step
    2.5) because the worker is a separate process from the FastAPI
    in-memory event bus. The publish is best-effort: a network blip
    must not stall the heartbeat or fail the activity.
    """
    from datetime import UTC, datetime

    from agora.platform.control_plane.pm_provision import resolve_workspace_root

    now = datetime.now(UTC)
    today = now.strftime("%Y-%m-%d")
    journal_dir = resolve_workspace_root() / payload.pm_id / "journals"
    # The provision activity already created this dir; re-mkdir is cheap
    # and protects against an out-of-order operator who clears the tree.
    journal_dir.mkdir(parents=True, exist_ok=True)
    journal = journal_dir / f"{today}.md"
    line = f"[{now.isoformat()}] [{payload.mode}]: alive\n"
    with journal.open("a", encoding="utf-8") as fh:
        fh.write(line)

    # Best-effort dashboard publish. Imported here (not at module top)
    # so the workflow sandbox validator never sees httpx at import time.
    await _publish_heartbeat(payload.pm_id, payload.mode, now.isoformat())


async def _publish_heartbeat(pm_id: str, mode: str, ts: str) -> None:
    """Best-effort publish to ``POST /api/internal/events``.

    Returns silently on any failure — the heartbeat already landed on
    disk, which is the source of truth. The dashboard event is a
    convenience surface; missing one is invisible to the system.

    Uses the process-lifetime httpx client (``workers/_http.py``) so
    we're not opening a fresh socket every cycle. The client builds
    lazily on first call; the worker's shutdown path closes it.
    """
    import httpx  # only for the exception type
    from loguru import logger

    from agora.platform.shared.settings import get_settings
    from agora.platform.workers._http import get_or_build_http_client

    settings = get_settings()
    if not settings.internal_event_token:
        # Token not configured → API rejects with 503. Skip the call so
        # we don't spam logs every cycle. Re-enable by setting
        # ``INTERNAL_EVENT_TOKEN`` in both the API and worker envs.
        return
    url = f"{settings.agora_api_url.rstrip('/')}/api/internal/events"
    body = {
        "type": "pm.heartbeat",
        "payload": {"pm_id": pm_id, "mode": mode, "ts": ts},
    }
    headers = {"x-agora-token": settings.internal_event_token}
    try:
        client = await get_or_build_http_client()
        await client.post(url, json=body, headers=headers)
    except httpx.HTTPError as e:
        logger.debug("heartbeat publish failed (non-fatal): {}", e)
    except Exception as e:  # defensive — never let teardown raise
        logger.debug("heartbeat publish failed (non-fatal): {}", e)


# ---------------------------------------------------------------- workflow


@workflow.defn(name="PMSupervisor")
class PMSupervisor:
    """Owns the per-PM lifecycle. One running workflow per PM."""

    def __init__(self) -> None:
        self._stopped = False
        self._paused = False

    @workflow.signal
    def stop(self) -> None:
        """Request a graceful exit. The loop notices on the next iteration."""
        self._stopped = True

    @workflow.signal
    def pause(self) -> None:
        """Skip heartbeats until ``resume``. Workflow stays alive."""
        self._paused = True

    @workflow.signal
    def resume(self) -> None:
        """Undo ``pause``. Heartbeats resume on the next loop iteration."""
        self._paused = False

    @workflow.run
    async def run(self, config: PMConfig) -> None:
        # 1) Idempotent provision. Important: this runs on every workflow
        #    start (and replay). The activity must not clobber agent
        #    state; see pm_provision.provision_workspace.
        await workflow.execute_activity(
            provision_pm_workspace,
            ProvisionInput(
                pm_id=config.pm_id,
                name=config.name,
                starting_capital_inr=config.starting_capital_inr,
            ),
            start_to_close_timeout=timedelta(seconds=10),
        )

        # 2) DB transition: spawned -> running.
        await workflow.execute_activity(
            mark_pm_running,
            config.pm_id,
            start_to_close_timeout=timedelta(seconds=10),
        )

        try:
            while not self._stopped:
                if self._paused:
                    # Paused: brief sleep so the loop is responsive to
                    # ``resume`` / ``stop`` without burning history events.
                    await workflow.sleep(timedelta(seconds=config.paused_poll_seconds))
                    continue

                mode = await workflow.execute_activity(
                    get_current_mode,
                    config.pm_id,
                    start_to_close_timeout=timedelta(seconds=5),
                )

                await workflow.execute_activity(
                    heartbeat_journal,
                    HeartbeatInput(pm_id=config.pm_id, mode=mode),
                    start_to_close_timeout=timedelta(seconds=10),
                )

                # Mode-aware cadence. Both default to 60s in K2 (per plan
                # §4 DoD #3); K3 may diverge. Choosing duration in the
                # workflow keeps replay deterministic.
                cycle_seconds = (
                    config.trading_cycle_seconds
                    if mode == "trading"
                    else config.build_cycle_seconds
                )
                await workflow.sleep(timedelta(seconds=cycle_seconds))
        finally:
            # Always run the stopped marker, even on cancellation. The
            # activity has its own short timeout — if it fails we'd
            # rather Temporal log a failure than hang the workflow.
            await workflow.execute_activity(
                mark_pm_stopped,
                config.pm_id,
                start_to_close_timeout=timedelta(seconds=10),
            )


__all__ = [
    "HeartbeatInput",
    "PMConfig",
    "PMSupervisor",
    "ProvisionInput",
    "get_current_mode",
    "heartbeat_journal",
    "mark_pm_running",
    "mark_pm_stopped",
    "provision_pm_workspace",
]
