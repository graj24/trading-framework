"""Strategy version registry — per-PM versioned strategy artefacts.

Each strategy is a YAML file at pm_<id>/strategies/v<n>.yaml.
The active version is tracked in pm_<id>/strategies/ACTIVE (plain text).

Usage:
    from common.strategy.registry import commit_new_version, load_active, diff

    v1 = {"name": "momentum", "watchlist": ["RELIANCE"], "gates": {"min_score": 6}}
    commit_new_version("1", v1, notes="initial strategy")
    active = load_active("1")
"""
from __future__ import annotations

import difflib
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

_BASE = Path(".")


def _strategies_dir(pm_id: str) -> Path:
    d = _BASE / f"pm_{pm_id}" / "strategies"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _active_path(pm_id: str) -> Path:
    return _strategies_dir(pm_id) / "ACTIVE"


def _version_path(pm_id: str, version: int) -> Path:
    return _strategies_dir(pm_id) / f"v{version:03d}.yaml"


def _next_version(pm_id: str) -> int:
    d = _strategies_dir(pm_id)
    existing = sorted(d.glob("v*.yaml"))
    if not existing:
        return 1
    last = existing[-1].stem  # e.g. "v003"
    return int(last[1:]) + 1


def commit_new_version(
    pm_id: str,
    strategy: dict[str, Any],
    parent_version: int | None = None,
    notes: str = "",
    set_active: bool = True,
) -> int:
    """Write a new strategy version. Returns the new version number."""
    version = _next_version(pm_id)
    strategy = dict(strategy)
    strategy["_meta"] = {
        "version": version,
        "parent_version": parent_version,
        "created_at": datetime.utcnow().isoformat(),
        "notes": notes,
    }
    path = _version_path(pm_id, version)
    path.write_text(yaml.dump(strategy, default_flow_style=False, allow_unicode=True))
    if set_active:
        _active_path(pm_id).write_text(str(version))
    logger.info(f"PM{pm_id} strategy v{version:03d} committed (active={set_active})")
    return version


def load_version(pm_id: str, version: int) -> dict[str, Any]:
    path = _version_path(pm_id, version)
    if not path.exists():
        raise FileNotFoundError(f"Strategy v{version:03d} not found for PM{pm_id}")
    return yaml.safe_load(path.read_text()) or {}


def load_active(pm_id: str) -> dict[str, Any] | None:
    """Load the currently active strategy. Returns None if none committed yet."""
    ap = _active_path(pm_id)
    if not ap.exists():
        return None
    try:
        version = int(ap.read_text().strip())
        return load_version(pm_id, version)
    except Exception as e:
        logger.warning(f"PM{pm_id} load_active failed: {e}")
        return None


def get_active_version(pm_id: str) -> int | None:
    ap = _active_path(pm_id)
    if not ap.exists():
        return None
    try:
        return int(ap.read_text().strip())
    except Exception:
        return None


def set_active_version(pm_id: str, version: int) -> None:
    if not _version_path(pm_id, version).exists():
        raise FileNotFoundError(f"Strategy v{version:03d} not found for PM{pm_id}")
    _active_path(pm_id).write_text(str(version))
    logger.info(f"PM{pm_id} active strategy set to v{version:03d}")


def list_versions(pm_id: str) -> list[dict]:
    """Return metadata for all versions, newest first."""
    d = _strategies_dir(pm_id)
    versions = []
    for path in sorted(d.glob("v*.yaml"), reverse=True):
        try:
            data = yaml.safe_load(path.read_text()) or {}
            meta = data.get("_meta", {})
            versions.append({
                "version": meta.get("version", int(path.stem[1:])),
                "created_at": meta.get("created_at", ""),
                "notes": meta.get("notes", ""),
                "parent_version": meta.get("parent_version"),
                "file": path.name,
            })
        except Exception:
            pass
    return versions


def diff(pm_id: str, version_a: int, version_b: int) -> str:
    """Return a unified diff between two strategy versions."""
    a = _version_path(pm_id, version_a)
    b = _version_path(pm_id, version_b)
    if not a.exists() or not b.exists():
        return "(one or both versions not found)"
    lines_a = a.read_text().splitlines(keepends=True)
    lines_b = b.read_text().splitlines(keepends=True)
    return "".join(difflib.unified_diff(
        lines_a, lines_b,
        fromfile=f"v{version_a:03d}.yaml",
        tofile=f"v{version_b:03d}.yaml",
    ))
