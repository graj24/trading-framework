"""FastAPI control-plane application.

Endpoints:

  GET  /api/health         — service liveness, returns 200 even when degraded so
                             monitoring distinguishes "API up, X sad" from "API dead".
  GET  /api/pms            — list PMs in spawn order.
  GET  /api/pms/{pm_id}    — fetch one PM record. 404 when missing.
  POST /api/pms/spawn      — provision a PM workspace and create the DB row
                             (status='spawned'). The Temporal workflow start
                             lands in K2 Step 2.2; ``workflow_id`` is null here.
  GET  /api/mode           — current AGORA operating mode (build / trading / freeze).

Cross-cutting:
  - Lifespan-scoped resources (Postgres pool, Temporal client, Langfuse SDK,
    shared httpx client) live on ``app.state.agora`` and are reused across
    requests. Built once at startup, torn down on shutdown.
  - DB-touching endpoints return 503 when the pool is None (e.g. Postgres
    was unavailable at app startup). The /api/health endpoint reports the
    same condition without erroring so monitoring can still poll it.
  - X-Request-ID middleware: pulls header or generates a UUID4, binds it onto
    loguru's contextualize() so every log line for that request carries it.
  - CORS for http://localhost:3000 (the dashboard).
  - Loguru configured per AGORA_LOG_FORMAT (json|human).
"""

from __future__ import annotations

import asyncio
import re
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from fastapi import (
    APIRouter,
    FastAPI,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger
from pydantic import BaseModel, Field

from agora.platform.control_plane import health, pm_provision, pm_repo
from agora.platform.control_plane.pm_repo import PMRecord, PMSummary
from agora.platform.control_plane.state import (
    AppState,
    build_app_state,
    teardown_app_state,
)
from agora.platform.control_plane.trade_repo import PaperTradeRecord
from agora.platform.observability.logging import configure_logging
from agora.platform.shared.settings import Settings, get_settings

REQUEST_ID_HEADER = "X-Request-ID"
# name must start with a letter; allow alnum + space + hyphen + underscore.
# 2-32 chars total. Mirrors the regex in plan/01-KEYSTONE.md §4 Step 2.1.
_PM_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9 _-]{1,31}$")
# Sane upper bound on starting capital — 1e9 INR is well above any plausible
# paper-trading allocation; reject above as 422 to catch unit-of-measure bugs.
_MAX_STARTING_CAPITAL_INR = 1e9


class ServiceHealth(BaseModel):
    status: health.Status
    detail: str


class HealthResponse(BaseModel):
    status: health.Status
    services: dict[str, ServiceHealth]


class SpawnPMRequest(BaseModel):
    """Body of ``POST /api/pms/spawn``."""

    name: str = Field(..., description="Display name. pm_id is derived from this.")
    starting_capital_inr: float = Field(..., gt=0, le=_MAX_STARTING_CAPITAL_INR)
    prompt_path: str | None = Field(
        default=None,
        description="Optional. Defaults to /dev/null until K7 ships PM persona prompts.",
    )


class SpawnPMResponse(BaseModel):
    """Body of the spawn endpoint response.

    ``workflow_id`` is the Temporal workflow id (``pm-<pm_id>``) of the
    just-started PMSupervisor workflow. The workflow itself flips the
    PM's status from ``spawned`` to ``running`` via ``mark_pm_running``
    on its first iteration.
    """

    pm_id: str
    workflow_id: str | None
    status: str
    workspace_path: str


class PMStateChangeResponse(BaseModel):
    """Body of ``POST /api/pms/{id}/{stop|pause|resume}``.

    ``status`` is the new lifecycle status the API has just driven the
    PM into (``stopped`` / ``paused`` / ``running``). The DB write here
    is optimistic; the workflow itself may also write the same row on
    graceful exit (e.g. ``mark_pm_stopped``). Whichever lands first
    wins; both produce the same value.
    """

    pm_id: str
    status: str


class JournalResponse(BaseModel):
    """Body of ``GET /api/pms/{id}/journal``.

    ``lines`` is ordered oldest-first, matching how the heartbeat
    activity appends. The dashboard reverses for "newest-first" if
    desired.
    """

    pm_id: str
    lines: list[str]


class InternalEventRequest(BaseModel):
    """Body of ``POST /api/internal/events`` (worker-process publish hook).

    K2 Step 2.5 Option A: the Temporal worker runs in a separate process
    from the FastAPI app, so it cannot reach the in-process EventBus
    directly. It POSTs here instead. K3+ may swap for NATS / LISTEN-NOTIFY;
    the wire shape stays.

    Type is plain ``str`` (not ``EventType``) so the worker doesn't have
    to import ``event_bus``. The bus broadcasts whatever it gets; the
    dashboard decides what to render.
    """

    type: str
    payload: dict[str, Any]


class ModeTransition(BaseModel):
    mode: str
    at: datetime


class ModeResponse(BaseModel):
    mode: str
    as_of: datetime
    next_transition: ModeTransition | None = None


def _aggregate_status(services: dict[str, ServiceHealth]) -> health.Status:
    """Worst-of: down > degraded > ok."""
    rank = {"ok": 0, "degraded": 1, "down": 2}
    inverse = {v: k for k, v in rank.items()}
    worst = max((rank[s.status] for s in services.values()), default=0)
    return inverse[worst]  # type: ignore[return-value]


def _get_state(request: Request) -> AppState:
    """Pull the lifespan-scoped resources off the app."""
    state: AppState = request.app.state.agora
    return state


def _build_router(settings: Settings) -> APIRouter:
    router = APIRouter(prefix="/api")

    @router.get("/health", response_model=HealthResponse)
    async def get_health(request: Request) -> HealthResponse:
        state = _get_state(request)
        # Run pings concurrently — each has its own 2s timeout.
        results = await asyncio.gather(
            health.ping_postgres(state.postgres_pool),
            health.ping_temporal(state.temporal_client, settings.temporal_namespace),
            health.ping_langfuse(
                state.langfuse,
                settings.langfuse_host,
                public_key=settings.langfuse_public_key,
                secret_key=settings.langfuse_secret_key,
            ),
            health.ping_letta(settings.letta_host, state.http_client),
            health.ping_qdrant(settings.qdrant_host, state.http_client),
            return_exceptions=False,
        )
        names = ["postgres", "temporal", "langfuse", "letta", "qdrant"]
        services: dict[str, ServiceHealth] = {
            name: ServiceHealth(status=status, detail=detail)
            for name, (status, detail) in zip(names, results, strict=True)
        }
        return HealthResponse(status=_aggregate_status(services), services=services)

    @router.get("/pms", response_model=list[PMSummary])
    async def list_pms(request: Request) -> list[PMSummary]:
        # The query goes through the shared asyncpg pool to avoid pulling
        # SQLAlchemy session machinery into the control plane before there's
        # a real model. 503 when the pool is unavailable (e.g. Postgres was
        # down at app startup) — uniform with the other DB-touching routes.
        state = _get_state(request)
        if state.postgres_pool is None:
            logger.warning("list_pms: postgres pool unavailable")
            raise HTTPException(status_code=503, detail="postgres unavailable")
        return await pm_repo.list_pms(state.postgres_pool)

    @router.get("/pms/{pm_id}", response_model=PMRecord)
    async def get_pm(pm_id: str, request: Request) -> PMRecord:
        state = _get_state(request)
        if state.postgres_pool is None:
            raise HTTPException(status_code=503, detail="postgres unavailable")
        record = await pm_repo.get_pm(state.postgres_pool, pm_id)
        if record is None:
            raise HTTPException(status_code=404, detail=f"pm {pm_id!r} not found")
        return record

    @router.get("/pms/{pm_id}/journal", response_model=JournalResponse)
    async def get_pm_journal(
        pm_id: str,
        request: Request,
        # Cap at 500 to avoid a DOS via huge query strings; default 50
        # matches the "last 50 entries" line in plan §4 Step 2.4.
        lines: int = Query(50, ge=1, le=500),
    ) -> JournalResponse:
        state = _get_state(request)
        if state.postgres_pool is None:
            raise HTTPException(status_code=503, detail="postgres unavailable")
        pm = await pm_repo.get_pm(state.postgres_pool, pm_id)
        if pm is None:
            raise HTTPException(status_code=404, detail=f"pm {pm_id!r} not found")
        workspace_root = pm_provision.resolve_workspace_root(state.settings)
        journal_lines = pm_provision.read_journal_tail(workspace_root / pm_id, lines=lines)
        return JournalResponse(pm_id=pm_id, lines=journal_lines)

    @router.get("/pms/{pm_id}/trades", response_model=list[PaperTradeRecord])
    async def get_pm_trades(
        pm_id: str,
        request: Request,
        # Mirrors the journal endpoint: 100 default, 500 cap. Same DoS
        # ceiling, same "tail of the ledger" semantics.
        limit: int = Query(100, ge=1, le=500),
    ) -> list[PaperTradeRecord]:
        from agora.platform.control_plane import trade_repo

        state = _get_state(request)
        if state.postgres_pool is None:
            raise HTTPException(status_code=503, detail="postgres unavailable")
        pm = await pm_repo.get_pm(state.postgres_pool, pm_id)
        if pm is None:
            raise HTTPException(status_code=404, detail=f"pm {pm_id!r} not found")
        return await trade_repo.list_trades(state.postgres_pool, pm_id, limit=limit)

    @router.post("/pms/spawn", response_model=SpawnPMResponse)
    async def spawn_pm(req: SpawnPMRequest, request: Request) -> SpawnPMResponse:
        state = _get_state(request)
        if state.postgres_pool is None:
            raise HTTPException(status_code=503, detail="postgres unavailable")
        if state.temporal_client is None:
            # Fail fast: spawning without Temporal would leave a PM row
            # whose workflow we can never start. The operator can retry
            # once Temporal is reachable.
            raise HTTPException(
                status_code=503,
                detail="temporal unavailable; PM cannot be spawned",
            )

        # Validate name + derive pm_id. Pydantic gave us type/range checks;
        # the regex is the alphabet rule from plan §4 Step 2.1.
        name = req.name
        if not _PM_NAME_RE.match(name):
            raise HTTPException(
                status_code=422,
                detail=(
                    "name must match ^[A-Za-z][A-Za-z0-9 _-]{1,31}$ "
                    "(2-32 chars, leading letter, alnum/space/-/_ only)"
                ),
            )
        pm_id = name.lower().replace(" ", "_").replace("-", "_")
        prompt_path = req.prompt_path or "/dev/null"

        # Duplicate-id guard. The PK on pms.id is the safety net; this
        # check turns the conflict into a clean 409 before we touch the
        # filesystem.
        if await pm_repo.pm_exists(state.postgres_pool, pm_id):
            raise HTTPException(status_code=409, detail=f"pm {pm_id!r} already exists")

        # 1) Insert row in 'provisioning' state.
        try:
            await pm_repo.insert_pm(
                state.postgres_pool,
                pm_id=pm_id,
                name=name,
                starting_capital_inr=req.starting_capital_inr,
                prompt_path=prompt_path,
                config={},
            )
        except Exception as e:  # asyncpg.UniqueViolationError or worse
            logger.exception("spawn_pm: insert_pm failed for {}: {}", pm_id, e)
            raise HTTPException(
                status_code=500,
                detail=f"failed to insert pm {pm_id!r}: {type(e).__name__}",
            ) from e

        # 2) Provision workspace (idempotent). On failure, best-effort
        # mark the row as 'error' so the dashboard surfaces the problem
        # instead of leaving the PM in 'provisioning' forever.
        try:
            workspace_root = pm_provision.resolve_workspace_root(state.settings)
            workspace_path = await pm_provision.provision_workspace(
                pm_id=pm_id,
                name=name,
                starting_capital_inr=req.starting_capital_inr,
                workspace_root=workspace_root,
                settings=state.settings,
            )
        except Exception as e:
            logger.exception("spawn_pm: provision_workspace failed for {}: {}", pm_id, e)
            try:
                await pm_repo.update_pm_status(state.postgres_pool, pm_id, "error")
            except Exception as inner:
                logger.warning("spawn_pm: also failed to mark {} as error: {}", pm_id, inner)
            raise HTTPException(
                status_code=500,
                detail=f"failed to provision workspace: {type(e).__name__}",
            ) from e

        # 3) Mark the row 'spawned'. The Temporal workflow start below
        # will flip it to 'running' on its own through mark_pm_running.
        await pm_repo.update_pm_status(state.postgres_pool, pm_id, "spawned")

        # 4) Start the PMSupervisor workflow. The workflow id is the
        # canonical handle the API uses for stop/pause/resume signals
        # in Step 2.3. On failure we mark the PM 'error' so the
        # dashboard surfaces it; the row stays so a retry is a clean
        # 409 (operator decides whether to delete it).
        from agora.platform.workers.pm_supervisor import PMConfig, PMSupervisor

        workflow_id = f"pm-{pm_id}"
        wf_config = PMConfig(
            pm_id=pm_id,
            name=name,
            starting_capital_inr=req.starting_capital_inr,
        )
        try:
            await state.temporal_client.start_workflow(
                PMSupervisor.run,
                wf_config,
                id=workflow_id,
                task_queue="agora",
            )
        except Exception as e:
            logger.exception("spawn_pm: start_workflow failed for {}: {}", pm_id, e)
            try:
                await pm_repo.update_pm_status(state.postgres_pool, pm_id, "error")
            except Exception as inner:
                logger.warning("spawn_pm: also failed to mark {} as error: {}", pm_id, inner)
            raise HTTPException(
                status_code=500,
                detail=f"failed to start workflow: {type(e).__name__}",
            ) from e

        # Persist the workflow id so /api/pms/{id} reflects it. If this
        # write fails (vanishingly unlikely — same pool we just used),
        # the workflow is still running; we surface 500 so the operator
        # knows to reconcile.
        try:
            await pm_repo.update_pm_workflow_id(state.postgres_pool, pm_id, workflow_id)
        except Exception as e:
            logger.exception("spawn_pm: update_pm_workflow_id failed for {}: {}", pm_id, e)
            raise HTTPException(
                status_code=500,
                detail=f"failed to persist workflow_id: {type(e).__name__}",
            ) from e

        # Tell the dashboard. Best-effort; never raises (the bus drops
        # on slow consumers and the publish itself doesn't await).
        await state.event_bus.publish(
            "agent.lifecycle",
            {
                "agent_id": pm_id,
                "pm_id": pm_id,
                "event": "started",
                "role": "pm",
            },
        )

        return SpawnPMResponse(
            pm_id=pm_id,
            workflow_id=workflow_id,
            status="spawned",
            workspace_path=str(workspace_path),
        )

    async def _resolve_handle_for_signal(state: AppState, pm_id: str) -> tuple[PMRecord, Any]:
        """Shared 503/404/409 dance for stop/pause/resume.

        Returns ``(pm_record, workflow_handle)``. Raises ``HTTPException``
        with the correct status code otherwise. Caller still owns the
        per-status idempotency / rejection rules; this helper only
        guarantees the PM exists and has a startable workflow handle.
        """
        if state.postgres_pool is None:
            raise HTTPException(status_code=503, detail="postgres unavailable")
        if state.temporal_client is None:
            raise HTTPException(status_code=503, detail="temporal unavailable")
        pm = await pm_repo.get_pm(state.postgres_pool, pm_id)
        if pm is None:
            raise HTTPException(status_code=404, detail=f"pm {pm_id!r} not found")
        if pm.workflow_id is None:
            raise HTTPException(
                status_code=409,
                detail=f"pm {pm_id!r} has no workflow_id (workflow never started)",
            )
        handle = state.temporal_client.get_workflow_handle(pm.workflow_id)
        return pm, handle

    @router.post("/pms/{pm_id}/stop", response_model=PMStateChangeResponse)
    async def stop_pm(pm_id: str, request: Request) -> PMStateChangeResponse:
        # Stop is a one-way door. Already-stopped PMs short-circuit to a
        # 200 no-op so retries from a flaky client are safe; everything
        # else funnels through the signal + optimistic DB write.
        from agora.platform.workers.pm_supervisor import PMSupervisor

        state = _get_state(request)
        pm, handle = await _resolve_handle_for_signal(state, pm_id)
        if pm.status == "stopped":
            return PMStateChangeResponse(pm_id=pm_id, status="stopped")
        try:
            await handle.signal(PMSupervisor.stop)
        except Exception as e:
            logger.exception("stop_pm: signal failed for {}: {}", pm_id, e)
            raise HTTPException(
                status_code=500,
                detail=f"failed to signal stop: {type(e).__name__}",
            ) from e
        # Optimistic DB write. The workflow's own ``mark_pm_stopped``
        # activity will also write 'stopped' on graceful exit; both
        # produce the same row, so the race is harmless.
        await pm_repo.update_pm_status(state.postgres_pool, pm_id, "stopped")
        await state.event_bus.publish(
            "agent.lifecycle",
            {
                "agent_id": pm_id,
                "pm_id": pm_id,
                "event": "stopped",
                "role": "pm",
            },
        )
        return PMStateChangeResponse(pm_id=pm_id, status="stopped")

    @router.post("/pms/{pm_id}/pause", response_model=PMStateChangeResponse)
    async def pause_pm(pm_id: str, request: Request) -> PMStateChangeResponse:
        # Pause is idempotent on paused (200, no re-signal). Stopped is
        # a terminal state — pausing a stopped PM is a 409. Any other
        # status (running / spawned) is allowed and signalled.
        from agora.platform.workers.pm_supervisor import PMSupervisor

        state = _get_state(request)
        pm, handle = await _resolve_handle_for_signal(state, pm_id)
        if pm.status == "stopped":
            raise HTTPException(
                status_code=409,
                detail=f"pm {pm_id!r} is stopped; cannot pause",
            )
        if pm.status == "paused":
            return PMStateChangeResponse(pm_id=pm_id, status="paused")
        try:
            await handle.signal(PMSupervisor.pause)
        except Exception as e:
            logger.exception("pause_pm: signal failed for {}: {}", pm_id, e)
            raise HTTPException(
                status_code=500,
                detail=f"failed to signal pause: {type(e).__name__}",
            ) from e
        await pm_repo.update_pm_status(state.postgres_pool, pm_id, "paused")
        return PMStateChangeResponse(pm_id=pm_id, status="paused")

    @router.post("/pms/{pm_id}/resume", response_model=PMStateChangeResponse)
    async def resume_pm(pm_id: str, request: Request) -> PMStateChangeResponse:
        # Resume is only valid from paused. There is nothing to resume
        # from running, and stopped is a one-way door — both reject 409.
        from agora.platform.workers.pm_supervisor import PMSupervisor

        state = _get_state(request)
        pm, handle = await _resolve_handle_for_signal(state, pm_id)
        if pm.status == "stopped":
            raise HTTPException(
                status_code=409,
                detail=f"pm {pm_id!r} is stopped; cannot resume",
            )
        if pm.status != "paused":
            raise HTTPException(
                status_code=409,
                detail=f"pm {pm_id!r} is {pm.status!r}; resume requires 'paused'",
            )
        try:
            await handle.signal(PMSupervisor.resume)
        except Exception as e:
            logger.exception("resume_pm: signal failed for {}: {}", pm_id, e)
            raise HTTPException(
                status_code=500,
                detail=f"failed to signal resume: {type(e).__name__}",
            ) from e
        await pm_repo.update_pm_status(state.postgres_pool, pm_id, "running")
        return PMStateChangeResponse(pm_id=pm_id, status="running")

    @router.get("/mode", response_model=ModeResponse)
    async def get_mode(request: Request) -> ModeResponse:
        from agora.platform.control_plane import mode as mode_module
        from agora.platform.control_plane.mode_loader import load_active_overrides

        state = _get_state(request)
        if state.postgres_pool is None:
            raise HTTPException(status_code=503, detail="postgres unavailable")
        now = datetime.now(UTC)
        overrides = await load_active_overrides(state.postgres_pool, now)
        result = mode_module.compute_mode(now, overrides=overrides)
        return ModeResponse(
            mode=result.mode,
            as_of=now,
            next_transition=(
                ModeTransition(mode=result.next_transition[0], at=result.next_transition[1])
                if result.next_transition is not None
                else None
            ),
        )

    @router.post("/internal/events", include_in_schema=False)
    async def internal_event(
        req: InternalEventRequest,
        request: Request,
        x_agora_token: str = Header(...),
    ) -> dict[str, bool]:
        # Token-protected publish hook for the worker process. Empty
        # configured token disables the route entirely (503) so that a
        # mis-deployed setup never accidentally accepts unauthenticated
        # publishes. We compare with == rather than secrets.compare_digest
        # because the token is local-only and not yet a security boundary;
        # if it becomes one in K3+, swap the comparison there.
        state = _get_state(request)
        configured = state.settings.internal_event_token
        if not configured:
            raise HTTPException(
                status_code=503,
                detail="internal events disabled (set internal_event_token)",
            )
        if x_agora_token != configured:
            raise HTTPException(status_code=401, detail="invalid token")
        await state.event_bus.publish(req.type, req.payload)
        return {"ok": True}

    return router


def _make_request_id_middleware() -> (
    Callable[[Request, Callable[[Request], Awaitable[Response]]], Awaitable[Response]]
):
    async def middleware(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request_id = request.headers.get(REQUEST_ID_HEADER) or str(uuid.uuid4())
        with logger.contextualize(request_id=request_id):
            response = await call_next(request)
        response.headers[REQUEST_ID_HEADER] = request_id
        return response

    return middleware


def _make_lifespan(
    settings: Settings,
) -> Callable[[FastAPI], AbstractAsyncContextManager[None]]:
    """Build the FastAPI lifespan that owns the AppState singletons."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        state = await build_app_state(settings)
        app.state.agora = state
        logger.info(
            "AGORA control plane resources built " "(postgres={pg} temporal={tc} langfuse={lf})",
            pg="ok" if state.postgres_pool is not None else "down",
            tc="ok" if state.temporal_client is not None else "down",
            lf="ok" if state.langfuse is not None else "off",
        )
        # K2 Step 2.5 — background task: poll the mode controller and
        # publish ``mode.changed`` on transitions. The loop owns its
        # own cancellation; failure to start (e.g. asyncio internals)
        # never blocks the API from coming up.
        mode_task = asyncio.create_task(
            _mode_change_loop(state),
            name="agora-mode-change-loop",
        )
        try:
            yield
        finally:
            mode_task.cancel()
            try:
                await mode_task
            except asyncio.CancelledError:
                pass
            except Exception as e:  # defensive — never let teardown raise
                logger.warning("mode_change_loop teardown raised: {}", e)
            await teardown_app_state(state)
            logger.info("AGORA control plane resources released")

    return lifespan


# Poll cadence for the mode-change event-publishing loop. K1 had a
# separate mode_loop.py that polled-and-logged; that module was
# deleted in post-audit/k2-5 once K2's event-publishing loop here
# took over. Kept literal so this file has no legacy dependency.
_MODE_POLL_INTERVAL_S: float = 30.0


async def _mode_change_loop(state: AppState) -> None:
    """Watch the mode controller and publish on transitions.

    Compares the latest computed mode to the previous tick; only the
    transition is announced. The loop swallows compute errors (Postgres
    blip, etc.) — the next tick gets a fresh shot. Cancellation is the
    only way out.
    """
    from agora.platform.control_plane import mode as mode_module
    from agora.platform.control_plane.mode_loader import load_active_overrides

    last_mode: str | None = None
    logger.info("mode_change_loop starting (poll every {}s)", _MODE_POLL_INTERVAL_S)
    try:
        while True:
            try:
                now = datetime.now(UTC)
                overrides = await load_active_overrides(state.postgres_pool, now)
                result = mode_module.compute_mode(now, overrides=overrides)
                if last_mode is not None and result.mode != last_mode:
                    await state.event_bus.publish(
                        "mode.changed",
                        {
                            "from": last_mode,
                            "to": result.mode,
                        },
                    )
                last_mode = result.mode
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning("mode_change_loop tick failed: {}", e)
            await asyncio.sleep(_MODE_POLL_INTERVAL_S)
    finally:
        logger.info("mode_change_loop stopping")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_format)

    app = FastAPI(
        title="AGORA Control Plane",
        version="0.0.1",
        description="Platform control plane for AGORA. See plan/00-FRAMEWORK.md.",
        lifespan=_make_lifespan(settings),
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.middleware("http")(_make_request_id_middleware())
    app.include_router(_build_router(settings))

    @app.get("/")
    async def root() -> dict[str, Any]:
        return {"service": "agora-control-plane", "version": app.version}

    @app.websocket("/api/stream")
    async def stream(ws: WebSocket) -> None:
        # Live activity stream (K2 Step 2.5). Subscribers are independent
        # — slow consumers drop events but never stall the publisher.
        # The ``async for`` body breaks on disconnect; on any other
        # exception we log and let the WS close — reconnect is the
        # subscriber's responsibility.
        state: AppState = ws.app.state.agora
        await ws.accept()
        try:
            async for event in state.event_bus.subscribe():
                await ws.send_json(
                    {
                        "type": event.type,
                        "ts": event.ts,
                        "payload": event.payload,
                    }
                )
        except WebSocketDisconnect:
            return
        except Exception as e:
            logger.warning("ws stream error: {}", e)

    logger.info("AGORA control plane initialized (log_format={})", settings.log_format)
    return app
