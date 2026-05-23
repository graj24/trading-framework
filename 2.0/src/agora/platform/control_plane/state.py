"""Lifespan-scoped resources for the AGORA control plane.

These singletons are built once when the FastAPI app boots (in the
``lifespan`` context manager) and reused across requests until shutdown.
A previous K1 cut opened a fresh asyncpg connection / Temporal gRPC
channel / Langfuse SDK on every request — fine for a smoke test, ruinous
under any load. Now each request just borrows from the pre-built pool
or holds a reference to the long-lived client.

If a backing service is unreachable at startup (Postgres down, Temporal
not yet up), the corresponding field stays ``None`` and the matching
``ping_*`` helper in ``health.py`` reports ``down`` from the health
endpoint. Boot is intentionally non-fatal: a control plane that won't
even come up is harder to diagnose than one whose health endpoint
truthfully says "postgres unavailable".
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import asyncpg
import httpx
from langfuse import Langfuse
from loguru import logger
from temporalio.client import Client as TemporalClient

from agora.platform.control_plane.event_bus import EventBus
from agora.platform.shared.settings import Settings

# Per-resource startup timeout. Tight on purpose — a degraded local stack
# should not make the API take 30s to start. The matching health checks
# pick up the slack and report which service did not come online.
STARTUP_TIMEOUT_S: float = 2.0


@dataclass(slots=True)
class AppState:
    """Process-lifetime singletons stashed on ``app.state.agora``.

    Any field can be ``None`` when its backing service was unreachable at
    startup; consumers are responsible for handling that and logging.
    """

    settings: Settings
    postgres_pool: asyncpg.Pool | None
    temporal_client: TemporalClient | None
    langfuse: Langfuse | None
    http_client: httpx.AsyncClient
    # K2 Step 2.5 — in-process pub/sub for the live activity stream.
    # Always present (no external service to fail), so consumers can
    # call ``state.event_bus.publish(...)`` without a None-check.
    event_bus: EventBus


async def build_app_state(settings: Settings) -> AppState:
    """Construct lifespan-scoped resources. Failures are logged, not raised."""
    pool = await _build_pool(settings)
    temporal_client = await _build_temporal_client(settings)
    langfuse = _build_langfuse(settings)
    http_client = httpx.AsyncClient(timeout=2.0)
    return AppState(
        settings=settings,
        postgres_pool=pool,
        temporal_client=temporal_client,
        langfuse=langfuse,
        http_client=http_client,
        event_bus=EventBus(),
    )


async def teardown_app_state(state: AppState) -> None:
    """Release lifespan-scoped resources. Best-effort; never raises."""
    if state.postgres_pool is not None:
        try:
            await state.postgres_pool.close()
        except Exception as e:
            logger.warning("postgres pool close raised: {}", e)
    if state.temporal_client is not None:
        try:
            # temporalio.client.Client itself has no public close(); the
            # underlying gRPC connection is private. Best-effort: try the
            # known internal attribute, swallow if the SDK ever moves it.
            connection = getattr(state.temporal_client, "connection", None)
            if connection is not None:
                close_fn = getattr(connection, "close", None)
                if callable(close_fn):
                    result = close_fn()
                    if hasattr(result, "__await__"):
                        await result
        except Exception as e:
            logger.warning("temporal client close raised: {}", e)
    try:
        await state.http_client.aclose()
    except Exception as e:
        logger.warning("http client close raised: {}", e)
    if state.langfuse is not None:
        # Langfuse 2.x has no async close. Flush is the best we can do —
        # then let GC drop the SDK object.
        try:
            state.langfuse.flush()
        except Exception as e:
            logger.warning("langfuse flush raised: {}", e)


async def _build_pool(settings: Settings) -> asyncpg.Pool | None:
    bare_url = settings.postgres_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    try:
        return await asyncio.wait_for(
            asyncpg.create_pool(bare_url, min_size=1, max_size=10),
            timeout=STARTUP_TIMEOUT_S,
        )
    except Exception as e:
        logger.warning(
            "asyncpg pool init failed; /api/health will report postgres down: {err}",
            err=f"{type(e).__name__}: {e}",
        )
        return None


async def _build_temporal_client(settings: Settings) -> TemporalClient | None:
    try:
        return await asyncio.wait_for(
            TemporalClient.connect(
                settings.temporal_host,
                namespace=settings.temporal_namespace,
            ),
            timeout=STARTUP_TIMEOUT_S,
        )
    except Exception as e:
        logger.warning(
            "temporal client connect failed; /api/health will report temporal down: {err}",
            err=f"{type(e).__name__}: {e}",
        )
        return None


def _build_langfuse(settings: Settings) -> Langfuse | None:
    if not settings.langfuse_public_key or not settings.langfuse_secret_key:
        return None
    try:
        return Langfuse(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
            host=settings.langfuse_host,
        )
    except Exception as e:
        logger.warning(
            "Langfuse SDK construction failed; /api/health will report down: {err}",
            err=f"{type(e).__name__}: {e}",
        )
        return None


__all__ = [
    "STARTUP_TIMEOUT_S",
    "AppState",
    "build_app_state",
    "teardown_app_state",
]
