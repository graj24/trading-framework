"""Per-PM watchlist resolution.

Each PM has pm_<id>/watchlist.yaml with a 'symbols' list.
Falls back to the global config watchlist if the PM file is empty or missing.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterable

import yaml

logger = logging.getLogger(__name__)


def get_pm_watchlist(pm_id: str, config: dict | None = None) -> list[str]:
    """Return the effective watchlist for a PM.

    Priority:
    1. pm_<id>/watchlist.yaml symbols (if non-empty)
    2. Global config watchlist (fallback)
    """
    wl_path = Path(f"pm_{pm_id}") / "watchlist.yaml"
    if wl_path.exists():
        try:
            data = yaml.safe_load(wl_path.read_text()) or {}
            symbols = data.get("symbols", [])
            if symbols:
                return [str(s) for s in symbols]
        except Exception as e:
            logger.warning(f"PM{pm_id} watchlist.yaml parse error: {e}")

    # Fallback to global config
    if config:
        from common.core.watchlist import resolve_watchlist
        return resolve_watchlist(config)
    return []


def set_pm_watchlist(pm_id: str, symbols: list[str]) -> None:
    """Write a PM's watchlist."""
    wl_path = Path(f"pm_{pm_id}") / "watchlist.yaml"
    wl_path.parent.mkdir(parents=True, exist_ok=True)
    wl_path.write_text(
        yaml.dump({"symbols": symbols}, default_flow_style=False, allow_unicode=True)
    )


def add_to_pm_watchlist(pm_id: str, symbols: Iterable[str]) -> list[str]:
    """Add symbols to a PM's watchlist. Returns newly added symbols."""
    existing = get_pm_watchlist(pm_id)
    seen = set(existing)
    new = [s for s in symbols if s and s not in seen]
    if new:
        set_pm_watchlist(pm_id, existing + new)
    return new
