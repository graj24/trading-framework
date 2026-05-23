"""Service ping helpers for the /api/health endpoint.

Each helper returns a `(status, detail)` tuple. Status is one of:

  ok        — service responded as expected
  degraded  — service is reachable in some sense but not fully usable
              (e.g. Langfuse keys missing, but app still boots)
  down      — service is unreachable / broken

`detail` is a short string suitable for inclusion in the JSON response. None of
these helpers raise; they trap errors and translate to "down". Each ping has
its own ~2s timeout.

Resources (Postgres pool, Temporal client, Langfuse SDK, shared httpx client)
are built once in the FastAPI ``lifespan`` and passed in as arguments. The
ping helpers do *not* construct these themselves — that fixes a K1 audit
finding where every /api/health call was opening fresh connections.
"""

from __future__ import annotations

import asyncio
from typing import Literal

import asyncpg
import httpx
from langfuse import Langfuse
from temporalio.client import Client as TemporalClient

Status = Literal["ok", "degraded", "down"]
PING_TIMEOUT_S: float = 2.0


async def ping_postgres(pool: asyncpg.Pool | None) -> tuple[Status, str]:
    """SELECT 1 against Postgres via the lifespan-scoped pool."""
    if pool is None:
        return "down", "no pool — postgres unavailable at app startup"
    try:
        async with pool.acquire() as conn:
            value = await asyncio.wait_for(conn.fetchval("SELECT 1"), timeout=PING_TIMEOUT_S)
        if value == 1:
            return "ok", "SELECT 1"
        return "down", f"unexpected SELECT 1 result: {value!r}"
    except TimeoutError:
        return "down", f"timeout after {PING_TIMEOUT_S}s"
    except Exception as e:
        return "down", f"{type(e).__name__}: {e}"


async def ping_temporal(client: TemporalClient | None, namespace: str) -> tuple[Status, str]:
    """Describe namespace via the lifespan-scoped Temporal client."""
    if client is None:
        return "down", "no client — temporal unavailable at app startup"
    try:
        await asyncio.wait_for(
            client.service_client.workflow_service.describe_namespace(
                _namespace_request(namespace)
            ),
            timeout=PING_TIMEOUT_S,
        )
        return "ok", f"namespace={namespace}"
    except TimeoutError:
        return "down", f"timeout after {PING_TIMEOUT_S}s"
    except Exception as e:
        return "down", f"{type(e).__name__}: {e}"


def _namespace_request(namespace: str):  # type: ignore[no-untyped-def]
    """Build a DescribeNamespaceRequest. Imported lazily for test ergonomics."""
    from temporalio.api.workflowservice.v1 import DescribeNamespaceRequest

    return DescribeNamespaceRequest(namespace=namespace)


async def ping_langfuse(
    client: Langfuse | None,
    host: str,
    *,
    public_key: str,
    secret_key: str,
) -> tuple[Status, str]:
    """Report Langfuse status using the lifespan-built SDK reference.

    We do NOT make a real HTTP call — Langfuse Cloud's availability is not
    part of our SLO. The states we distinguish:

      * keys missing               → degraded (app boots, no traces will land)
      * keys present, SDK is None  → down     (construction failed at startup)
      * keys present, SDK present  → ok       (sdk ready)
    """
    if not public_key or not secret_key:
        return "degraded", "missing LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY"
    if client is None:
        return "down", "sdk construction failed at startup"
    return "ok", f"sdk ready (host={host})"


async def ping_letta(host: str, http_client: httpx.AsyncClient) -> tuple[Status, str]:
    """GET {host}/v1/health/."""
    return await _http_get_ok(f"{host.rstrip('/')}/v1/health/", http_client)


async def ping_qdrant(host: str, http_client: httpx.AsyncClient) -> tuple[Status, str]:
    """GET {host}/healthz."""
    return await _http_get_ok(f"{host.rstrip('/')}/healthz", http_client)


async def _http_get_ok(url: str, client: httpx.AsyncClient) -> tuple[Status, str]:
    try:
        response = await client.get(url, timeout=PING_TIMEOUT_S)
        if response.status_code < 400:
            return "ok", f"GET {url} -> {response.status_code}"
        return "down", f"GET {url} -> {response.status_code}"
    except httpx.TimeoutException:
        return "down", f"timeout after {PING_TIMEOUT_S}s"
    except Exception as e:
        return "down", f"{type(e).__name__}: {e}"
