"""Centralised configuration loader.

Until 2026-05-16, every module that needed ``config.yaml`` read it
independently — `risk_manager.py`, `core/scheduler.py`, multiple CLIs.
That made hot-reload implicit and accidental, and tests had to monkey-patch
many locations.

This module exposes a single ``get_config()`` accessor that:

* Caches the parsed dict for the process lifetime.
* Lets tests inject a custom path or replacement dict via ``set_config()``.
* Falls back to a minimal default if `config.yaml` is missing (so tests
  can construct agents without setting up the file).
"""
from __future__ import annotations

import functools
import os
from pathlib import Path
from typing import Any

import yaml


_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_DEFAULT_PATH = _REPO_ROOT / "config.yaml"

_DEFAULTS: dict[str, Any] = {
    "trading":  {"mode": "paper", "capital": 10000, "currency": "INR"},
    "watchlist": [],
    "core_watchlist": [],
    "watchlist_max": 20,
    "risk": {
        "kelly_fraction": 0.5,
        "max_loss_per_trade_pct": 1.0,
        "max_loss_per_day_pct": 3.0,
        "max_loss_per_week_pct": 7.0,
        "max_loss_per_month_pct": 15.0,
        "max_open_positions": 3,
        "trailing_stop_trigger_pct": 1.0,
        "trailing_stop_distance_pct": 0.5,
        "close_all_time": "15:00",
    },
    "llm": {"model": "openai/moonshotai/kimi-k2.6",
            "api_base": "https://integrate.api.nvidia.com/v1",
            "max_tokens": 200, "temperature": 0.1},
    "logging": {"level": "INFO", "file": "logs/trading.log",
                "max_bytes": 10_485_760, "backup_count": 5},
    "data": {"history_years": 5, "timeframes": ["1d", "1h", "15m", "5m"]},
    "schedule": {
        "pre_market_data": "06:00",
        "pre_market_analysis": "08:30",
        "market_open_signals": "09:00",
        "market_open_execute": "09:15",
        "intraday_interval_minutes": 5,
        "post_market": "15:30",
    },
    "telegram": {"enabled": False},
}

# Overlay slot for tests / runtime injection.
_OVERRIDE: dict | None = None
_OVERRIDE_PATH: Path | None = None


def get_config(path: Path | str | None = None) -> dict:
    """Return the runtime configuration as a dict.

    Cached after first call. Pass ``path`` to force a reload from a
    specific file (mainly for tests). For programmatic injection in
    tests, prefer ``set_config(...)``.
    """
    if _OVERRIDE is not None:
        return _OVERRIDE
    p = Path(path) if path is not None else (_OVERRIDE_PATH or _DEFAULT_PATH)
    return _load(p)


@functools.lru_cache(maxsize=8)
def _load(path: Path) -> dict:
    if not path.exists():
        return dict(_DEFAULTS)
    with open(path) as f:
        loaded = yaml.safe_load(f) or {}
    # Shallow-merge with defaults so partial configs still work.
    merged = {**_DEFAULTS, **loaded}
    for k, v in _DEFAULTS.items():
        if isinstance(v, dict) and isinstance(merged.get(k), dict):
            merged[k] = {**v, **merged[k]}
    return merged


def set_config(config: dict | None, path: Path | str | None = None) -> None:
    """Test hook. ``set_config(d)`` makes ``get_config()`` return ``d``.
    Pass ``None`` to clear the override and resume normal lookups."""
    global _OVERRIDE, _OVERRIDE_PATH
    _OVERRIDE = config
    _OVERRIDE_PATH = Path(path) if path is not None else None
    _load.cache_clear()


def reload_config() -> dict:
    """Force a re-read from disk (drops the lru_cache)."""
    global _OVERRIDE
    _OVERRIDE = None
    _load.cache_clear()
    return get_config()
