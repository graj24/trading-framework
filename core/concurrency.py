"""Concurrency helpers for fan-out work across symbols.

The MasterAgent fan-out is network-bound (yfinance, news scraping, NSE).
Threads work fine — there's no Python-side hot path. This module provides
a small wrapper so callers don't have to think about executor lifecycle.

See docs/analysis/06-improvements.md P1 §15 (which this fix lands).
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Iterable, TypeVar

logger = logging.getLogger("trading.concurrency")

T = TypeVar("T")
U = TypeVar("U")


def map_symbols(
    fn: Callable[[T], U],
    items: Iterable[T],
    *,
    max_workers: int = 5,
    label: str = "task",
) -> dict[T, U | BaseException]:
    """Run ``fn`` on every item concurrently. Returns a mapping
    ``{item: result_or_exception}`` so callers can decide what to do
    with partial failures.

    Defaults to 5 worker threads — empirically the sweet spot before
    yfinance starts rate-limiting the test environment.
    """
    items = list(items)
    if not items:
        return {}
    out: dict[T, U | BaseException] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(fn, item): item for item in items}
        for fut in as_completed(futures):
            item = futures[fut]
            try:
                out[item] = fut.result()
            except BaseException as exc:  # noqa: BLE001
                logger.warning("concurrency | %s failed for %r: %s", label, item, exc)
                out[item] = exc
    return out
