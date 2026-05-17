"""Helpers for reading values out of heterogeneous row containers.

`sqlite3.Row` does NOT implement `.get()`, but plenty of callers in this
codebase (e.g. `main.py:135-138`) treat rows like dicts. This module
provides a single `row_get()` helper that works uniformly across:

* `sqlite3.Row`              — column access via `row[key]` if `key in row.keys()`
* `dict` / `Mapping`         — standard `.get()`
* anything supporting `.get()` — delegates to it
* `None`                      — returns the default

This file deliberately has no third-party dependencies so it can be imported
from anywhere (including test fixtures) without pulling pandas / yfinance.
"""
from __future__ import annotations

import sqlite3
from typing import Any


def row_get(row: Any, key: str, default: Any = None) -> Any:
    """Return ``row[key]`` if available, otherwise ``default``.

    Designed to fix the long-standing bug in ``main.py`` where the closed-trade
    learning loop assumed ``sqlite3.Row.get()`` exists. It does not.

    Args:
        row: a ``sqlite3.Row``, a ``dict``-like, an object with a ``.get`` method,
            or ``None``.
        key: column / dict key.
        default: returned when the key is missing or the row is ``None``.

    Returns:
        The value for ``key`` if present, otherwise ``default``.
    """
    if row is None:
        return default

    # sqlite3.Row: must check .keys() because indexing a missing column raises
    # IndexError, not KeyError.
    if isinstance(row, sqlite3.Row):
        if key in row.keys():
            return row[key]
        return default

    # Dict-like: native .get() does the right thing.
    if hasattr(row, "get") and callable(row.get):  # type: ignore[union-attr]
        return row.get(key, default)  # type: ignore[union-attr]

    # Fallback: try indexing.
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return default
