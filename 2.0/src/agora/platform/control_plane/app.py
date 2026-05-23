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

from fastapi import APIRouter, FastAPI, HTTPException, Request, Response
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

        return SpawnPMResponse(
            pm_id=pm_id,
            workflow_id=workflow_id,
            status="spawned",
            workspace_path=str(workspace_path),
        )

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
        try:
            yield
        finally:
            await teardown_app_state(state)
            logger.info("AGORA control plane resources released")

    return lifespan


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

    logger.info("AGORA control plane initialized (log_format={})", settings.log_format)
    return app
