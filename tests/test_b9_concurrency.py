"""Tests for B.9 — `core.concurrency.map_symbols`."""
from __future__ import annotations

import time

from core.concurrency import map_symbols


def test_map_symbols_runs_in_parallel():
    """5 items × 0.1s sleep should finish in well under 0.3s with workers=5."""
    def slow(_x):
        time.sleep(0.1)
        return _x * 2

    t0 = time.perf_counter()
    out = map_symbols(slow, [1, 2, 3, 4, 5], max_workers=5)
    elapsed = time.perf_counter() - t0
    assert out == {1: 2, 2: 4, 3: 6, 4: 8, 5: 10}
    assert elapsed < 0.3, f"expected parallelism, took {elapsed:.2f}s"


def test_map_symbols_returns_exceptions_per_item():
    def maybe_fails(x):
        if x == 3:
            raise ValueError("boom on 3")
        return x

    out = map_symbols(maybe_fails, [1, 2, 3, 4])
    assert out[1] == 1
    assert isinstance(out[3], ValueError)
    assert str(out[3]) == "boom on 3"


def test_map_symbols_empty_input():
    assert map_symbols(lambda x: x, []) == {}
