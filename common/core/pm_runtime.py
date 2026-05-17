"""
PM runtime — register PMs, provision workspaces, list active PMs.

Usage:
  from core.pm_runtime import register_pm, list_pms, get_pm_config

  register_pm("1", prompt_path="pm_prompts/PM1_full_prompt.md")
  register_pm("2", prompt_path="pm_prompts/PM2_full_prompt.md")
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

import yaml

from common.core.pm_state import write_plan, write_tasks, write_team, write_positions

logger = logging.getLogger(__name__)

REGISTRY_PATH = Path("pm_registry.json")

DEFAULT_PLAN = """# Strategy Plan
- Review market regime and watchlist
- Identify high-conviction setups
- Delegate research to Researcher, execution to Trader, risk checks to Risk
- Update this plan after each shift
"""

DEFAULT_TASKS = {
    "backlog": [],
    "in_progress": [],
    "done": [],
}

DEFAULT_TEAM = {
    "researcher": {"role": "Deep research, data gathering, sector analysis"},
    "trader":     {"role": "Order construction and execution"},
    "risk":       {"role": "Pre-trade checks, VaR, exposure monitoring"},
}


def _registry() -> dict:
    if REGISTRY_PATH.exists():
        return json.loads(REGISTRY_PATH.read_text())
    return {}


def _save_registry(reg: dict):
    REGISTRY_PATH.write_text(json.dumps(reg, indent=2))


def register_pm(
    pm_id: str,
    prompt_path: str | None = None,
    config_overrides: dict | None = None,
    copy_from: str | None = None,
) -> Path:
    """
    Provision workspace for a PM and add it to the registry.
    Returns the workspace root path.
    """
    ws = Path(f"pm_{pm_id}")
    state_dir = ws / "state"
    agents_dir = ws / "agents"
    journal_archive = state_dir / "journal_archive"
    strategies_dir = ws / "strategies"
    prompts_dir = ws / "prompts"
    data_sources_dir = ws / "data_sources"
    models_dir = ws / "models"

    for d in [state_dir, agents_dir, journal_archive, strategies_dir,
              prompts_dir, data_sources_dir, models_dir]:
        d.mkdir(parents=True, exist_ok=True)

    # __init__.py stubs so PM-specific packages are importable
    for pkg_dir in [agents_dir, data_sources_dir]:
        init = pkg_dir / "__init__.py"
        if not init.exists():
            init.write_text(f"# PM{pm_id} private package\n")

    # Seed state files if they don't exist
    if not (state_dir / "plan.md").exists():
        write_plan(pm_id, DEFAULT_PLAN)
    if not (state_dir / "tasks.yaml").exists():
        write_tasks(pm_id, DEFAULT_TASKS)
    if not (state_dir / "team.yaml").exists():
        write_team(pm_id, DEFAULT_TEAM)
    if not (state_dir / "positions.json").exists():
        write_positions(pm_id, [])
    for f in ["inbox.jsonl", "proposals.jsonl"]:
        p = state_dir / f
        if not p.exists():
            p.touch()
    for f in ["journal.md", "journal_summary.md"]:
        p = state_dir / f
        if not p.exists():
            p.write_text(f"# PM{pm_id} {f.replace('.md','').replace('_',' ').title()}\n")

    # PM-specific config
    cfg_path = ws / "config.yaml"
    if not cfg_path.exists():
        base_cfg = {
            "pm_id": pm_id,
            "capital": 10000,
            "max_open_positions": 3,
            "daily_loss_halt_pct": 3.0,
            "weekly_loss_halve_pct": 7.0,
            "triage_poll_interval_sec": 5,
            "heartbeat_shifts": ["08:30", "09:15", "11:00", "12:30", "14:00", "15:30"],
        }
        if config_overrides:
            base_cfg.update(config_overrides)
        cfg_path.write_text(yaml.dump(base_cfg, default_flow_style=False))

    # Seed watchlist
    wl_path = ws / "watchlist.yaml"
    if not wl_path.exists():
        if copy_from:
            src_wl = Path(f"pm_{copy_from}") / "watchlist.yaml"
            if src_wl.exists():
                import shutil
                shutil.copy(src_wl, wl_path)
            else:
                wl_path.write_text("# PM watchlist — add symbols or leave empty for dynamic discovery\nsymbols: []\n")
        else:
            wl_path.write_text("# PM watchlist — add symbols or leave empty for dynamic discovery\nsymbols: []\n")

    # Seed blank strategy v001 if no strategies exist yet
    if not list(strategies_dir.glob("v*.yaml")):
        from common.strategy.registry import commit_new_version
        blank_strategy: dict = {
            "name": "blank",
            "description": "Cold-start blank strategy. PM will evolve this on first cycle.",
            "watchlist": [],
            "pipeline": "passive_observe",
            "gates": {},
            "sizing": {"method": "fixed", "amount": 1000},
            "data_sources": ["yfinance"],
            "autonomy": {
                "can_short": False,
                "can_fno": False,
                "universe": "any_nse",
            },
        }
        if copy_from:
            src_active = Path(f"pm_{copy_from}") / "strategies" / "ACTIVE"
            if src_active.exists():
                try:
                    src_ver = int(src_active.read_text().strip())
                    src_path = Path(f"pm_{copy_from}") / "strategies" / f"v{src_ver:03d}.yaml"
                    if src_path.exists():
                        blank_strategy = yaml.safe_load(src_path.read_text()) or blank_strategy
                        blank_strategy.pop("_meta", None)
                        blank_strategy["description"] = f"Copied from PM{copy_from} v{src_ver:03d}"
                except Exception:
                    pass
        commit_new_version(pm_id, blank_strategy, notes=f"cold-start seed (copy_from={copy_from})")

    # Registry entry
    reg = _registry()
    reg[pm_id] = {
        "pm_id": pm_id,
        "workspace": str(ws),
        "prompt_path": prompt_path,
        "registered_at": datetime.utcnow().isoformat(),
        "active": True,
    }
    _save_registry(reg)

    logger.info(f"PM{pm_id} registered — workspace: {ws}")
    return ws


def list_pms(active_only: bool = True) -> list[dict]:
    reg = _registry()
    pms = list(reg.values())
    if active_only:
        pms = [p for p in pms if p.get("active", True)]
    return pms


def get_pm_config(pm_id: str) -> dict:
    cfg_path = Path(f"pm_{pm_id}") / "config.yaml"
    if not cfg_path.exists():
        return {}
    return yaml.safe_load(cfg_path.read_text()) or {}


def deactivate_pm(pm_id: str):
    reg = _registry()
    if pm_id in reg:
        reg[pm_id]["active"] = False
        _save_registry(reg)
        logger.info(f"PM{pm_id} deactivated")
