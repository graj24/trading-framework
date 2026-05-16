"""Watchlist resolution + dynamic-watchlist persistence (LOW-10).

Until 2026-05-16, ``DiscoveryAgent`` and ``PreOpenMonitor`` mutated
``config.yaml`` directly via ``yaml.dump``, which destroyed comments and
re-ordered keys. This module provides a clean separation:

* ``config.yaml`` is **read-only** for the daemon (user-curated).
* Dynamic discoveries are persisted to ``data/dynamic_watchlist.json``.
* The effective watchlist used for runtime is the union of
  ``config.core_watchlist`` and the dynamic file, capped at
  ``config.watchlist_max``.

See docs-verification/findings.md LOW-10 and docs/analysis/05-issues.md §B12.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

DEFAULT_DYNAMIC_PATH = Path("data") / "dynamic_watchlist.json"


def _read_dynamic(path: Path) -> list[str]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return []
    if isinstance(data, list):
        return [str(s) for s in data if s]
    return []


def _write_dynamic(path: Path, symbols: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(symbols, indent=2))


def resolve_watchlist(
    config: dict,
    dynamic_path: Path | str = DEFAULT_DYNAMIC_PATH,
) -> list[str]:
    """Return the effective watchlist for this run.

    Order of precedence:
    1. Every symbol in ``config.core_watchlist`` (preserves order).
    2. Then every symbol in the dynamic file (preserves order).
    3. Finally — for backwards compatibility — anything in ``config.watchlist``
       that wasn't already included.

    Duplicates are removed, then the result is capped at ``config.watchlist_max``
    (default 20).
    """
    core = list(config.get("core_watchlist", []) or [])
    dyn = _read_dynamic(Path(dynamic_path))
    legacy = list(config.get("watchlist", []) or [])

    seen: set[str] = set()
    effective: list[str] = []
    for s in (*core, *dyn, *legacy):
        if s and s not in seen:
            seen.add(s)
            effective.append(s)

    cap = int(config.get("watchlist_max", 20))
    return effective[:cap]


def add_to_dynamic_watchlist(
    symbols: Iterable[str],
    dynamic_path: Path | str = DEFAULT_DYNAMIC_PATH,
) -> list[str]:
    """Append ``symbols`` to the dynamic watchlist file.

    Returns the list of symbols that were genuinely new (i.e. not already
    present in the file). Never touches ``config.yaml``.
    """
    path = Path(dynamic_path)
    existing = _read_dynamic(path)
    seen = set(existing)
    new: list[str] = []
    for s in symbols:
        if s and s not in seen:
            seen.add(s)
            existing.append(s)
            new.append(s)
    if new:
        _write_dynamic(path, existing)
    return new
