"""Tests for the worker-process market-data singleton.

Mirrors the rationale of ``test_workers_http`` for the HTTP client and
the implicit pool reuse in ``test_pm_supervisor``: the K3.6 audit
identified that ``trading_cycle_activity`` was building a fresh
:class:`ParquetMarketData` per invocation, throwing away the
per-instance frame cache. The post-audit fix lifts the adapter to a
process-lifetime singleton (``workers/_market_data.py``).

These tests pin the singleton contract:

* ``test_singleton_returns_same_instance`` — two calls return the
  same object, so any per-instance cache survives.
* ``test_reset_drops_cache`` — ``reset_market_data()`` clears the
  cached instance, used between tests so module state doesn't bleed
  into other suites.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from agora.platform.workers._market_data import (
    get_or_build_market_data,
    reset_market_data,
)


@pytest.fixture(autouse=True)
def _reset_module_state() -> Iterator[None]:
    """Drop the cached adapter before and after every test in this module."""
    reset_market_data()
    yield
    reset_market_data()


async def test_singleton_returns_same_instance() -> None:
    first = await get_or_build_market_data()
    second = await get_or_build_market_data()
    assert first is second


async def test_reset_drops_cache() -> None:
    first = await get_or_build_market_data()
    reset_market_data()
    second = await get_or_build_market_data()
    assert first is not second
