"""Idempotent PM workspace provisioning.

Step 2.1 of K2: create ``<workspace_root>/<pm_id>/`` with the canonical
directory tree and seed files. Provisioning must be idempotent because:

  1. Step 2.2's PMSupervisor workflow re-runs the provision activity on
     every workflow restart per Temporal's at-least-once activity
     contract; overwriting the PM's plan/config on each restart would
     erase the agent's memory.
  2. The plan §4 explicitly calls this out as a tripwire.

Existing files are left untouched. Existing directories are accepted
silently (``mkdir -p`` semantics). Only files we are about to create
fresh get content; if a journal for today already exists we don't even
``touch`` it (preserving mtime).

Path-traversal guard: ``pm_id`` is constrained to a small alphabet at
the API boundary, but we re-validate here so a mis-wired call site
cannot escape the workspace root.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from loguru import logger

from agora.platform.shared.settings import Settings, get_settings

# pm_id alphabet: lowercase ASCII, digits, underscore. Conservative on
# purpose — anything else would fight us across journals/, config files,
# Temporal workflow ids, and Postgres queries.
_PM_ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,31}$")

# Repo-2.0 root resolved from this file: src/agora/platform/control_plane/
# is 4 directories deep below 2.0/.
_REPO_2_0 = Path(__file__).resolve().parents[4]


def _validate_pm_id(pm_id: str) -> None:
    """Reject pm_ids that could escape the workspace or break path joins.

    The endpoint already constrains ``name`` and derives ``pm_id`` from
    it via ``.lower().replace(...)``; this is defence in depth so a
    direct caller (e.g. a future CLI) cannot smuggle ``..`` or ``/``.
    """
    if not isinstance(pm_id, str) or not _PM_ID_RE.match(pm_id):
        raise ValueError(f"invalid pm_id {pm_id!r}: must match {_PM_ID_RE.pattern}")


def resolve_workspace_root(settings: Settings | None = None) -> Path:
    """Return the directory under which all PM workspaces live.

    Order:
      1. Explicit ``workspace_root`` from settings (env: ``WORKSPACE_ROOT``).
      2. ``<repo-2.0>/pms`` resolved from this module's ``__file__``.
    """
    settings = settings or get_settings()
    if settings.workspace_root:
        return Path(settings.workspace_root).resolve()
    return _REPO_2_0 / "pms"


def _seed_config_yaml(
    name: str,
    starting_capital_inr: float,
    spawned_at: datetime,
    settings: Settings,
) -> str:
    """Render the seed config.yaml content. Plain string formatting —
    pyyaml is a project dep but the seed has no nested structures and
    introducing yaml-dump indeterminacy here would obscure diffs.

    Cadence keys (``build_cycle_seconds`` / ``trading_cycle_seconds``)
    match the dataclass field names so :func:`load_pm_config` can
    pass the parsed dict straight into ``PMConfig`` overrides.
    """
    return (
        "# Generated at spawn. The PM may overwrite this in K4+ (Reflection).\n"
        f"name: {name}\n"
        f"spawned_at: {spawned_at.isoformat()}\n"
        f"starting_capital_inr: {starting_capital_inr}\n"
        "build_cycle_seconds: 60\n"
        "trading_cycle_seconds: 60\n"
        "daily_budget_usd_build: 20.0\n"
        "daily_budget_usd_trading: 5.0\n"
        f"default_reasoning_model: {settings.agora_default_reasoning_model}\n"
        f"default_cheap_model: {settings.agora_default_cheap_model}\n"
    )


async def provision_workspace(
    pm_id: str,
    name: str,
    starting_capital_inr: float,
    workspace_root: Path | None = None,
    settings: Settings | None = None,
) -> Path:
    """Idempotently create the PM's workspace tree.

    Returns the absolute path to the PM's directory. Skips files that
    already exist (Temporal at-least-once safety).
    """
    _validate_pm_id(pm_id)
    settings = settings or get_settings()
    root = (workspace_root or resolve_workspace_root(settings)).resolve()
    pm_dir = (root / pm_id).resolve()

    # Belt-and-braces: the validated pm_id cannot escape, but the explicit
    # check makes the guarantee local to this function.
    if not str(pm_dir).startswith(str(root)):
        raise ValueError(f"resolved pm_dir {pm_dir} escapes workspace root {root}")

    spawned_at = datetime.now(UTC)
    today = spawned_at.strftime("%Y-%m-%d")

    # Directory tree — mkdir(parents=True, exist_ok=True) is the idempotent
    # primitive we want. Every directory we want must exist after this loop.
    subdirs = ("plans", "journals", "strategies", "research", "code")
    pm_dir.mkdir(parents=True, exist_ok=True)
    for sub in subdirs:
        (pm_dir / sub).mkdir(parents=True, exist_ok=True)

    # Seed files — write only if missing. The "skip if exists" branch is
    # the load-bearing one: re-provisioning must NOT clobber the PM's
    # journal or evolved plan.
    plan_file = pm_dir / "plans" / "current.md"
    if not plan_file.exists():
        plan_file.write_text(
            f"PM {name} initialized at {spawned_at.isoformat()}.\n",
            encoding="utf-8",
        )

    journal_file = pm_dir / "journals" / f"{today}.md"
    if not journal_file.exists():
        journal_file.write_text("", encoding="utf-8")

    config_file = pm_dir / "config.yaml"
    if not config_file.exists():
        config_file.write_text(
            _seed_config_yaml(name, starting_capital_inr, spawned_at, settings),
            encoding="utf-8",
        )

    return pm_dir


def load_pm_config(pm_workspace: Path) -> dict[str, Any]:
    """Read ``config.yaml`` from the PM workspace.

    Returns ``{}`` if the file is missing or malformed so callers can
    fall back to dataclass defaults. Logs a warning on parse error —
    we don't want an operator typo in YAML to fail-stop the workflow.

    Closes the source-of-truth drift between the seed YAML and
    ``PMConfig``: K2 wrote the seed at provision time but never read
    it back. The provision activity now calls this helper and pipes
    the cadence values into the workflow's sleep durations so operator
    edits to ``config.yaml`` take effect on the next workflow restart.
    """
    config_file = pm_workspace / "config.yaml"
    if not config_file.exists():
        return {}
    try:
        parsed = yaml.safe_load(config_file.read_text(encoding="utf-8"))
    except Exception as e:
        # Bad YAML: log + ignore. Don't fail the workflow on operator typos.
        logger.warning("load_pm_config: failed to parse {}: {}", config_file, e)
        return {}
    return parsed if isinstance(parsed, dict) else {}


def read_journal_tail(pm_workspace_root: Path, *, lines: int = 50) -> list[str]:
    """Return the last ``lines`` entries of *today's* journal for one PM.

    Returns ``[]`` if today's journal file doesn't exist yet (PM hasn't
    logged anything since UTC midnight) or the workspace itself is
    missing. The caller — the dashboard journal endpoint — treats the
    empty case as "nothing to show", not as an error.

    The "today" boundary follows the rule used by the heartbeat
    activity (``YYYY-MM-DD`` in UTC); they must agree or the dashboard
    will look at the wrong file across the midnight roll-over.
    """
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    journal_path = pm_workspace_root / "journals" / f"{today}.md"
    if not journal_path.exists():
        return []
    text = journal_path.read_text(encoding="utf-8")
    all_lines = text.splitlines()
    return all_lines[-lines:] if len(all_lines) > lines else all_lines


__all__ = ["load_pm_config", "provision_workspace", "read_journal_tail", "resolve_workspace_root"]
