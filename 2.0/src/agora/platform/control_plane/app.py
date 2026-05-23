"""FastAPI control-plane application.

Endpoints:

  GET /api/health  — service liveness, returns 200 even when degraded so
                     monitoring distinguishes "API up, X sad" from "API dead".
  GET /api/pms     — list PMs (empty in K1; the table exists, no rows yet).
  GET /api/mode    — current AGORA operating mode (build / trading / freeze).

Cross-cutting:
  - Lifespan-scoped resources (Postgres pool, Temporal client, Langfuse SDK,
    shared httpx client) live on ``app.state.agora`` and are reused across
    requests. Built once at startup, torn down on shutdown.
  - X-Request-ID middleware: pulls header or generates a UUID4, binds it onto
    loguru's contextualize() so every log line for that request carries it.
  - CORS for http://localhost:3000 (the dashboard).
  - Loguru configured per AGORA_LOG_FORMAT (json|human).
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger
from pydantic import BaseModel

from agora.platform.control_plane import health
from agora.platform.control_plane.state import (
    AppState,
    build_app_state,
    teardown_app_state,
)
from agora.platform.observability.logging import configure_logging
from agora.platform.shared.settings import Settings, get_settings

REQUEST_ID_HEADER = "X-Request-ID"


class ServiceHealth(BaseModel):
    status: health.Status
    detail: str


class HealthResponse(BaseModel):
    status: health.Status
    services: dict[str, ServiceHealth]


class PMSummary(BaseModel):
    """Public PM record. Empty list in K1; populated in K2."""

    id: str
    name: str
    status: str


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
        # K1: schema exists, no rows. The query goes through the shared
        # asyncpg pool to avoid pulling SQLAlchemy session machinery into the
        # control plane before there's a real model.
        state = _get_state(request)
        if state.postgres_pool is None:
            logger.warning("list_pms: postgres pool unavailable; returning []")
            return []
        async with state.postgres_pool.acquire() as conn:
            rows = await conn.fetch("SELECT id, name, status FROM pms ORDER BY spawned_at")
        return [PMSummary(id=r["id"], name=r["name"], status=r["status"]) for r in rows]

    @router.get("/mode", response_model=ModeResponse)
    async def get_mode(request: Request) -> ModeResponse:
        from agora.platform.control_plane import mode as mode_module
        from agora.platform.control_plane.mode_loader import load_active_overrides

        state = _get_state(request)
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
