"""Process-lifetime market data adapter for worker activities.

Mirrors :mod:`agora.platform.workers._pool`: one
:class:`~agora.apps.propfirm.data.nse.ParquetMarketData` per worker
process, lazily built on first use, cleared on shutdown. The adapter
caches parquet frames per-instance, so reusing it across trading
cycles avoids re-reading the same file every minute.

The K3.5 ``trading_cycle_activity`` and the K3.6 ``eod_close_activity``
both build a default adapter when the caller doesn't pass one. Going
through this helper turns the per-invocation construction (and its
empty cache) into a per-worker singleton.

Sandbox safety
--------------
Like ``_pool.py``, this module is **never** imported from a workflow
module — only from activity bodies. The activity body is outside the
sandbox; the workflow module top is inside. Routing the adapter
construction through this helper keeps the supervisor module clean.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agora.apps.propfirm.data.nse import MarketDataAdapter

_adapter: MarketDataAdapter | None = None
_lock = asyncio.Lock()


async def get_or_build_market_data() -> MarketDataAdapter:
    """Return the worker-process-wide market-data adapter.

    Builds a fresh :class:`ParquetMarketData` on first call; subsequent
    calls return the same instance so its per-instance frame cache
    survives across activity invocations. Concurrent first-callers
    race on ``_lock``; only one builds.
    """
    global _adapter
    if _adapter is not None:
        return _adapter
    async with _lock:
        if _adapter is not None:
            return _adapter
        # Lazy import: pulls in pandas/numpy. Activity body only —
        # never the workflow module top.
        from agora.apps.propfirm.data.nse import ParquetMarketData

        _adapter = ParquetMarketData()
    return _adapter


def reset_market_data() -> None:
    """Drop the cached adapter. For tests; not used in production."""
    global _adapter
    _adapter = None


__all__ = ["get_or_build_market_data", "reset_market_data"]
