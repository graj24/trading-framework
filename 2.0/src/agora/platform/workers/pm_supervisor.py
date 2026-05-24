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
from temporalio.common import RetryPolicy

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
class ProvisionResult:
    """Return shape of the provision activity.

    The workflow can't read files (sandbox), so the provision activity
    loads ``config.yaml`` and hands the cadence values back to the
    workflow as plain ints. Operator edits to YAML take effect on the
    next workflow restart, which is what the file's existence implied.
    """

    workspace_path: str
    build_cycle_seconds: int
    trading_cycle_seconds: int


@dataclass
class HeartbeatInput:
    """Args for the heartbeat activity."""

    pm_id: str
    mode: str  # "build" | "trading" | "pre_trade_freeze"


@dataclass
class TradingCycleInput:
    """Args for the trading-cycle activity (K3 Step 3.5)."""

    pm_id: str


@dataclass
class TradingCycleOutput:
    """Result shape for the trading-cycle activity.

    All four lists are plain JSON (ints / strings) so the workflow can
    read them across the activity boundary without touching the
    sandbox-forbidden imports that produced them. Mirrors
    :class:`agora.apps.propfirm.trading.cycle.CycleResult` minus the
    ``pm_id`` (which the workflow already has on hand).
    """

    placed: list[int]
    closed: list[int]
    skipped: list[str]
    rejected: list[str]


@dataclass
class EodCloseInput:
    """Args for the EOD close activity (K3 Step 3.6)."""

    pm_id: str


@dataclass
class EodCloseOutput:
    """Result shape for the EOD close activity.

    Same JSON-shape discipline as :class:`TradingCycleOutput`: plain
    ints / strings cross the workflow boundary so the
    :class:`EodCloser` workflow doesn't have to import
    :class:`~agora.apps.propfirm.trading.eod.EodCloseResult` (which
    pulls in asyncpg via :mod:`trade_repo`).
    """

    closed: list[int]
    skipped: list[str]


# --------------------------------------------------------------- activities
# Activities run on the worker process, outside the workflow sandbox.
# ALL non-stdlib imports must be deferred to function bodies so the
# workflow validator (which re-imports this module) does not see them.


@activity.defn(name="provision_pm_workspace")
async def provision_pm_workspace(payload: ProvisionInput) -> ProvisionResult:
    """Idempotently (re-)create the PM's workspace tree.

    Returns the resolved workspace path along with the cadence values
    loaded from the workspace's ``config.yaml`` (falling back to
    PMConfig defaults if the file is missing a key or malformed).
    Runs on every workflow start (including replays). The provisioner
    is mkdir-p + write-if-absent under the hood, so repeats are safe.
    """
    from agora.platform.control_plane.pm_provision import (
        load_pm_config,
        provision_workspace,
    )

    pm_dir = await provision_workspace(
        pm_id=payload.pm_id,
        name=payload.name,
        starting_capital_inr=payload.starting_capital_inr,
    )
    cfg = load_pm_config(pm_dir)
    # PMConfig defaults — keep these in sync with the dataclass below.
    # We don't import PMConfig() defaults here because that would
    # introduce a top-of-module workflow-side dependency cycle.
    build_default = 60
    trading_default = 60

    def _coerce_int(value: object, fallback: int) -> int:
        """YAML can return ``int`` directly. Anything else (str, float,
        None, list) falls back to the dataclass default — we don't try
        to be clever about coercion; an operator who wants seconds
        should write seconds."""
        if isinstance(value, int) and not isinstance(value, bool):
            return value
        return fallback

    return ProvisionResult(
        workspace_path=str(pm_dir),
        build_cycle_seconds=_coerce_int(cfg.get("build_cycle_seconds"), build_default),
        trading_cycle_seconds=_coerce_int(cfg.get("trading_cycle_seconds"), trading_default),
    )


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

    from agora.platform.shared.journal import journal_append

    now = datetime.now(UTC)
    line = f"[{now.isoformat()}] [{payload.mode}]: alive"
    # Centralised journal helper (post-K2 audit refactor): handles
    # workspace-root resolution, mkdir-if-missing, UTC-bounded
    # filename, and append-only write. Same on-disk content as before.
    journal_append(payload.pm_id, line, now=now)

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


@activity.defn(name="trading_cycle")
async def trading_cycle_activity(payload: TradingCycleInput) -> TradingCycleOutput:
    """Run one trading cycle for a PM (K3 Step 3.5).

    Defers heavy imports inside the body to keep the workflow sandbox
    clean. The cycle module pulls in NautilusTrader (via the parquet
    market data adapter), pandas, asyncpg, and the broker — none of
    which the workflow validator can see at import time.

    The activity body itself runs outside the sandbox; the sandbox-
    safety property is the absence of these imports at module top.
    See module docstring for the workflow/activity sandbox split.
    """
    from agora.apps.propfirm.trading.cycle import run_trading_cycle
    from agora.platform.workers._market_data import get_or_build_market_data
    from agora.platform.workers._pool import get_or_build_pool

    pool = await get_or_build_pool()
    market_data = await get_or_build_market_data()
    result = await run_trading_cycle(pool, payload.pm_id, market_data=market_data)
    return TradingCycleOutput(
        placed=list(result.placed),
        closed=list(result.closed),
        skipped=list(result.skipped),
        rejected=list(result.rejected),
    )


@activity.defn(name="eod_close")
async def eod_close_activity(payload: EodCloseInput) -> EodCloseOutput:
    """Close every open paper position for one PM at the latest price.

    K3 Step 3.6. Defers heavy imports inside the body to keep the
    workflow sandbox clean. The closer pulls in pandas/numpy via the
    parquet market-data adapter and asyncpg via :mod:`trade_repo` —
    none of which the workflow validator can see at import time.

    Idempotency: the closer takes the snapshot of currently-open
    trades and closes each one. A re-execution after a successful
    pass finds zero open trades and returns an empty result, so
    Temporal's retry-on-failure is safe. We still cap retries via
    :class:`RetryPolicy` at the workflow site so a wedged adapter
    doesn't churn forever.
    """
    from agora.apps.propfirm.trading.eod import close_positions_for_pm
    from agora.platform.workers._market_data import get_or_build_market_data
    from agora.platform.workers._pool import get_or_build_pool

    pool = await get_or_build_pool()
    market_data = await get_or_build_market_data()
    result = await close_positions_for_pm(pool, payload.pm_id, market_data=market_data)
    return EodCloseOutput(
        closed=list(result.closed),
        skipped=list(result.skipped),
    )


@activity.defn(name="list_running_pms")
async def list_running_pms_activity() -> list[str]:
    """Return every PM currently in ``status='running'``.

    K3 Step 3.6 — the :class:`EodCloser` workflow uses this to
    discover what to close. Workflow code can't talk to the DB, so the
    list-of-pms query is an activity. Returns plain ``list[str]`` (PM
    ids) so the workflow boundary is JSON-only.
    """
    from agora.platform.control_plane.pm_repo import list_pms
    from agora.platform.workers._pool import get_or_build_pool

    pool = await get_or_build_pool()
    pms = await list_pms(pool)
    return [pm.id for pm in pms if pm.status == "running"]


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
        #
        #    The activity also reads ``config.yaml`` (workflow code
        #    cannot — file I/O isn't allowed in the sandbox) and hands
        #    back the cadence values, so operator edits to YAML take
        #    effect on the next workflow restart. The values fall back
        #    to ``PMConfig`` defaults if the file is missing a key.
        #    Precedence: YAML on disk > PMConfig defaults. The spawn
        #    endpoint doesn't pass cadence overrides, so production
        #    sees YAML cadence; tests that need a non-default cadence
        #    pre-write ``config.yaml`` (the idempotent provisioner
        #    leaves it alone).
        provisioned = await workflow.execute_activity(
            provision_pm_workspace,
            ProvisionInput(
                pm_id=config.pm_id,
                name=config.name,
                starting_capital_inr=config.starting_capital_inr,
            ),
            start_to_close_timeout=timedelta(seconds=10),
        )
        build_cycle_seconds = provisioned.build_cycle_seconds
        trading_cycle_seconds = provisioned.trading_cycle_seconds

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

                if mode == "trading":
                    # K3.5: real trading cycle. The activity loads
                    # market data, runs the momentum signal, and
                    # places/closes orders via the AGORA broker. It
                    # journals each placement/skip/rejection, so we
                    # do NOT fall through to the heartbeat below
                    # (the cycle's own journal lines replace the
                    # plain "alive" tick).
                    #
                    # Generous timeout: the cycle does N market-data
                    # reads + a list_open_trades + a write per
                    # symbol. 60s gives ~10x headroom on a 10-symbol
                    # NIFTY watchlist.
                    #
                    # No retries: same reasoning as the heartbeat —
                    # the activity's writes (broker.submit_order,
                    # close_trade) are not idempotent. Re-running on
                    # transient failure could double-place an order.
                    # The next cycle one cadence period later is the
                    # right recovery path.
                    await workflow.execute_activity(
                        trading_cycle_activity,
                        TradingCycleInput(pm_id=config.pm_id),
                        start_to_close_timeout=timedelta(seconds=60),
                        retry_policy=RetryPolicy(maximum_attempts=1),
                    )
                else:
                    # Build / pre_trade_freeze: K2 placeholder
                    # heartbeat. Trading work happens only in
                    # trading mode; the rest of the day the PM is
                    # alive but not trading.
                    await workflow.execute_activity(
                        heartbeat_journal,
                        HeartbeatInput(pm_id=config.pm_id, mode=mode),
                        # Bumped from 10s: the activity does a file
                        # write + an HTTP POST + (best-effort) DB
                        # writes. 10s gave only 3-4x headroom; a
                        # transient API slowdown could trip it. 30s
                        # gives ~10x.
                        start_to_close_timeout=timedelta(seconds=30),
                        # Heartbeats are best-effort signals, not
                        # guaranteed delivery. Temporal's default
                        # retry policy would re-run a slow tick and
                        # produce duplicate journal lines (the
                        # activity uses ``open("a")`` — append-only,
                        # no idempotency key). The next cycle's
                        # heartbeat (one cadence period later) is
                        # the right way to recover from a missed
                        # tick.
                        retry_policy=RetryPolicy(maximum_attempts=1),
                    )

                # Mode-aware cadence. Both default to 60s in K2 (per plan
                # §4 DoD #3); K3 may diverge. Choosing duration in the
                # workflow keeps replay deterministic. Values come from
                # the provision activity (loaded from config.yaml).
                cycle_seconds = trading_cycle_seconds if mode == "trading" else build_cycle_seconds
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


@workflow.defn(name="EodCloser")
class EodCloser:
    """End-of-day position closer (K3 Step 3.6).

    A scheduled workflow run once per trading day at 09:55 UTC
    (= 15:25 IST, five minutes before NSE close). For every PM in
    ``status='running'``, calls the EOD close activity which walks
    that PM's open trades and closes them at the latest available
    price (``outcome='eod_close'``).

    Sequential, not parallel: the PM count in K3 is small and
    sequential awaits are always replay-safe. Going parallel inside
    a workflow needs care for determinism (futures/asyncio.gather
    with mixed completion order can drift); the simpler path is the
    right one until the leaderboard has dozens of PMs.
    """

    @workflow.run
    async def run(self) -> None:
        pm_ids = await workflow.execute_activity(
            list_running_pms_activity,
            start_to_close_timeout=timedelta(seconds=10),
        )
        for pm_id in pm_ids:
            # Generous per-PM timeout: the closer does N market-data
            # snapshots + N close_trade writes; 120s gives ~10x
            # headroom on a 10-symbol portfolio. ``maximum_attempts=2``
            # so a transient adapter blip retries once but a stuck PM
            # doesn't churn the whole closer.
            await workflow.execute_activity(
                eod_close_activity,
                EodCloseInput(pm_id=pm_id),
                start_to_close_timeout=timedelta(seconds=120),
                retry_policy=RetryPolicy(maximum_attempts=2),
            )


__all__ = [
    "EodCloseInput",
    "EodCloseOutput",
    "EodCloser",
    "HeartbeatInput",
    "PMConfig",
    "PMSupervisor",
    "ProvisionInput",
    "ProvisionResult",
    "TradingCycleInput",
    "TradingCycleOutput",
    "eod_close_activity",
    "get_current_mode",
    "heartbeat_journal",
    "list_running_pms_activity",
    "mark_pm_running",
    "mark_pm_stopped",
    "provision_pm_workspace",
    "trading_cycle_activity",
]
