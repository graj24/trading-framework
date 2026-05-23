"""Service ping helpers for the /api/health endpoint.

Each helper returns a `(status, detail)` tuple. Status is one of:

  ok        — service responded as expected
  degraded  — service is reachable in some sense but not fully usable
              (e.g. Langfuse keys missing, but app still boots)
  down      — service is unreachable / broken

`detail` is a short string suitable for inclusion in the JSON response. None of
these helpers raise; they trap errors and translate to "down". Each ping has
its own ~2s timeout.

Postgres uses asyncpg directly (cheap, no SQLAlchemy session). Temporal uses
the temporalio gRPC client. Langfuse only verifies that keys are present and
the SDK is constructible — we deliberately do NOT make a real HTTP call,
because Langfuse Cloud's status is not our problem to monitor. Letta and
Qdrant are simple HTTP GETs via httpx.
"""

from __future__ import annotations

import asyncio
from typing import Literal

import httpx

Status = Literal["ok", "degraded", "down"]
PING_TIMEOUT_S: float = 2.0


async def ping_postgres(url: str) -> tuple[Status, str]:
    """SELECT 1 against Postgres via asyncpg."""
    # Lazy import so the module is testable without asyncpg drivers configured.
    import asyncpg

    # asyncpg expects a plain postgresql:// URL, not the SQLAlchemy +asyncpg form.
    bare_url = url.replace("postgresql+asyncpg://", "postgresql://", 1)
    try:
        conn = await asyncio.wait_for(asyncpg.connect(bare_url), timeout=PING_TIMEOUT_S)
        try:
            value = await asyncio.wait_for(conn.fetchval("SELECT 1"), timeout=PING_TIMEOUT_S)
        finally:
            await conn.close()
        if value == 1:
            return "ok", "SELECT 1"
        return "down", f"unexpected SELECT 1 result: {value!r}"
    except TimeoutError:
        return "down", f"timeout after {PING_TIMEOUT_S}s"
    except Exception as e:
        return "down", f"{type(e).__name__}: {e}"


async def ping_temporal(host: str) -> tuple[Status, str]:
    """Connect to Temporal and list namespaces."""
    from temporalio.client import Client

    try:
        client = await asyncio.wait_for(Client.connect(host), timeout=PING_TIMEOUT_S)
        # describe_namespace is the lightest call that proves end-to-end gRPC.
        await asyncio.wait_for(
            client.service_client.workflow_service.describe_namespace(
                _namespace_request(client.namespace)
            ),
            timeout=PING_TIMEOUT_S,
        )
        return "ok", f"namespace={client.namespace}"
    except TimeoutError:
        return "down", f"timeout after {PING_TIMEOUT_S}s"
    except Exception as e:
        return "down", f"{type(e).__name__}: {e}"


def _namespace_request(namespace: str):  # type: ignore[no-untyped-def]
    """Build a DescribeNamespaceRequest. Imported lazily for test ergonomics."""
    from temporalio.api.workflowservice.v1 import DescribeNamespaceRequest

    return DescribeNamespaceRequest(namespace=namespace)


async def ping_langfuse(host: str, public_key: str, secret_key: str) -> tuple[Status, str]:
    """Verify env keys are present and the Langfuse SDK can be instantiated.

    We do NOT make a real HTTP call. Langfuse Cloud's availability is not part
    of our SLO; if its keys are configured and the SDK constructs, we treat it
    as 'ok'. Missing keys = 'degraded' (app boots, but no traces will land).
    """
    if not public_key or not secret_key:
        return "degraded", "missing LANGFUSE_PUBLIC_KEY or LANGFUSE_SECRET_KEY"
    try:
        from langfuse import Langfuse

        Langfuse(public_key=public_key, secret_key=secret_key, host=host)
    except Exception as e:
        return "down", f"{type(e).__name__}: {e}"
    return "ok", f"sdk ready (host={host})"


async def ping_letta(host: str) -> tuple[Status, str]:
    """GET {host}/v1/health/."""
    return await _http_get_ok(f"{host.rstrip('/')}/v1/health/")


async def ping_qdrant(host: str) -> tuple[Status, str]:
    """GET {host}/healthz."""
    return await _http_get_ok(f"{host.rstrip('/')}/healthz")


async def _http_get_ok(url: str) -> tuple[Status, str]:
    try:
        async with httpx.AsyncClient(timeout=PING_TIMEOUT_S) as client:
            response = await client.get(url)
        if response.status_code < 400:
            return "ok", f"GET {url} -> {response.status_code}"
        return "down", f"GET {url} -> {response.status_code}"
    except httpx.TimeoutException:
        return "down", f"timeout after {PING_TIMEOUT_S}s"
    except Exception as e:
        return "down", f"{type(e).__name__}: {e}"
