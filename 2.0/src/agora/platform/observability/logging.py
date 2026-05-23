"""Loguru configuration for AGORA.

Two presets:

  human  — coloured, line-per-record, easy to read locally.
  json   — one JSON object per line, suitable for log aggregators.

Every log record carries the AGORA standard context fields: request_id,
agent_id, pm_id, task_id. They default to empty strings if the surrounding
code did not set them. Use `logger.contextualize(...)` (typically inside
middleware or activity wrappers) to populate them per-request / per-task.
"""

from __future__ import annotations

import sys
from typing import Any

from loguru import logger as _logger

CONTEXT_DEFAULTS: dict[str, str] = {
    "request_id": "",
    "agent_id": "",
    "pm_id": "",
    "task_id": "",
}


def _patch_defaults(record: dict[str, Any]) -> None:
    """Ensure every record has the AGORA context keys, even when not bound."""
    extra = record.setdefault("extra", {})
    for key, default in CONTEXT_DEFAULTS.items():
        extra.setdefault(key, default)


def configure_logging(format_: str = "human") -> None:
    """Idempotently configure the loguru logger."""
    _logger.remove()
    _logger.configure(patcher=_patch_defaults)  # type: ignore[arg-type]

    if format_ == "json":
        _logger.add(sys.stderr, serialize=True, level="INFO")
    else:
        fmt = (
            "<green>{time:HH:mm:ss}</green> "
            "<level>{level: <7}</level> "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan> "
            "[req={extra[request_id]} pm={extra[pm_id]} agent={extra[agent_id]}] "
            "<level>{message}</level>"
        )
        _logger.add(sys.stderr, format=fmt, level="INFO", colorize=True)


__all__ = ["CONTEXT_DEFAULTS", "configure_logging"]
