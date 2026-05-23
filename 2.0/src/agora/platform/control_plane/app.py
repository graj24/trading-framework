"""FastAPI control-plane application.

Endpoints:

  GET /api/health  — service liveness, returns 200 even when degraded so
                     monitoring distinguishes "API up, X sad" from "API dead".
  GET /api/pms     — list PMs (empty in K1; the table exists, no rows yet).
  GET /api/mode    — current AGORA operating mode (build / trading / freeze).

Cross-cutting:
  - X-Request-ID middleware: pulls header or generates a UUID4, binds it onto
    loguru's contextualize() so every log line for that request carries it.
  - CORS for http://localhost:3000 (the dashboard).
  - Loguru configured per AGORA_LOG_FORMAT (json|human).
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger
from pydantic import BaseModel

from agora.platform.control_plane import health
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


def _build_router(settings: Settings) -> APIRouter:
    router = APIRouter(prefix="/api")

    @router.get("/health", response_model=HealthResponse)
    async def get_health() -> HealthResponse:
        # Run pings concurrently — each has its own 2s timeout.
        import asyncio

        results = await asyncio.gather(
            health.ping_postgres(settings.postgres_url),
            health.ping_temporal(settings.temporal_host),
            health.ping_langfuse(
                settings.langfuse_host,
                settings.langfuse_public_key,
                settings.langfuse_secret_key,
            ),
            health.ping_letta(settings.letta_host),
            health.ping_qdrant(settings.qdrant_host),
            return_exceptions=False,
        )
        names = ["postgres", "temporal", "langfuse", "letta", "qdrant"]
        services: dict[str, ServiceHealth] = {
            name: ServiceHealth(status=status, detail=detail)
            for name, (status, detail) in zip(names, results, strict=True)
        }
        return HealthResponse(status=_aggregate_status(services), services=services)

    @router.get("/pms", response_model=list[PMSummary])
    async def list_pms() -> list[PMSummary]:
        # K1: schema exists, no rows. The query goes through asyncpg directly
        # to avoid pulling SQLAlchemy session machinery into the control plane
        # before there's a real model.
        import asyncpg

        bare_url = settings.postgres_url.replace("postgresql+asyncpg://", "postgresql://", 1)
        try:
            conn = await asyncpg.connect(bare_url)
        except Exception as e:
            logger.warning("list_pms could not connect to postgres: {}", e)
            return []
        try:
            rows = await conn.fetch("SELECT id, name, status FROM pms ORDER BY spawned_at")
        finally:
            await conn.close()
        return [PMSummary(id=r["id"], name=r["name"], status=r["status"]) for r in rows]

    @router.get("/mode", response_model=ModeResponse)
    async def get_mode() -> ModeResponse:
        # K1.4: returns 'build' as a placeholder. K1.5 wires this to the real
        # mode controller in agora.platform.control_plane.mode.
        from agora.platform.control_plane import mode as mode_module

        now = datetime.now(UTC)
        result = mode_module.compute_mode(now)
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


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_format)

    app = FastAPI(
        title="AGORA Control Plane",
        version="0.0.1",
        description="Platform control plane for AGORA. See plan/00-FRAMEWORK.md.",
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
