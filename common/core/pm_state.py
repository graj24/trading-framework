"""
PM workspace helpers — read/write per-PM state files.

Workspace layout:
  pm_<id>/
    state/
      plan.md
      tasks.yaml
      journal.md
      journal_summary.md
      inbox.jsonl
      positions.json
      proposals.jsonl
    agents/
    config.yaml
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

try:
    from zoneinfo import ZoneInfo
    IST = ZoneInfo("Asia/Kolkata")
except ImportError:
    from datetime import timezone, timedelta
    IST = timezone(timedelta(hours=5, minutes=30))

from common.core import migrations  # noqa: F401  schema migration

BASE = Path(".")


def _ws(pm_id: str) -> Path:
    return BASE / f"pm_{pm_id}" / "state"


# ── Read helpers ──────────────────────────────────────────────────────────────

def read_plan(pm_id: str) -> str:
    p = _ws(pm_id) / "plan.md"
    return p.read_text() if p.exists() else ""


def read_tasks(pm_id: str) -> dict:
    p = _ws(pm_id) / "tasks.yaml"
    if not p.exists():
        return {"backlog": [], "in_progress": [], "done": []}
    return yaml.safe_load(p.read_text()) or {}


def read_journal(pm_id: str, days: int = 7) -> str:
    p = _ws(pm_id) / "journal.md"
    return p.read_text() if p.exists() else ""


def read_journal_summary(pm_id: str) -> str:
    p = _ws(pm_id) / "journal_summary.md"
    return p.read_text() if p.exists() else ""


def read_inbox(pm_id: str) -> list[dict]:
    p = _ws(pm_id) / "inbox.jsonl"
    if not p.exists():
        return []
    events = []
    for line in p.read_text().splitlines():
        line = line.strip()
        if line:
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return events


def read_positions(pm_id: str) -> list[dict]:
    """Read cached positions snapshot (refreshed by pm_risk daemon)."""
    p = _ws(pm_id) / "positions.json"
    if not p.exists():
        return []
    return json.loads(p.read_text())


def read_proposals(pm_id: str) -> list[dict]:
    p = _ws(pm_id) / "proposals.jsonl"
    if not p.exists():
        return []
    out = []
    for line in p.read_text().splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return out


def read_team(pm_id: str) -> dict:
    p = _ws(pm_id) / "team.yaml"
    if not p.exists():
        return {}
    return yaml.safe_load(p.read_text()) or {}


# ── Write helpers ─────────────────────────────────────────────────────────────

def write_plan(pm_id: str, content: str):
    p = _ws(pm_id) / "plan.md"
    p.write_text(content)


def write_tasks(pm_id: str, tasks: dict):
    p = _ws(pm_id) / "tasks.yaml"
    p.write_text(yaml.dump(tasks, default_flow_style=False, allow_unicode=True))


def append_journal(pm_id: str, entry: str):
    p = _ws(pm_id) / "journal.md"
    ts = datetime.now(IST).strftime("%Y-%m-%d %H:%M IST")
    p.open("a").write(f"\n## {ts}\n{entry.strip()}\n")


def write_journal_summary(pm_id: str, content: str):
    p = _ws(pm_id) / "journal_summary.md"
    p.write_text(content)


def push_inbox(pm_id: str, event: dict):
    """Append one event to inbox (called by Tier 1 publishers)."""
    p = _ws(pm_id) / "inbox.jsonl"
    with p.open("a") as f:
        f.write(json.dumps(event) + "\n")


def drain_inbox(pm_id: str) -> list[dict]:
    """Read all inbox events and clear the file."""
    events = read_inbox(pm_id)
    p = _ws(pm_id) / "inbox.jsonl"
    p.write_text("")
    return events


def write_positions(pm_id: str, positions: list[dict]):
    p = _ws(pm_id) / "positions.json"
    p.write_text(json.dumps(positions, indent=2))


def push_proposal(pm_id: str, proposal: dict):
    """Queue a trade proposal (used in live mode for PM approval)."""
    proposal.setdefault("ts", datetime.utcnow().isoformat())
    proposal.setdefault("status", "pending")
    p = _ws(pm_id) / "proposals.jsonl"
    with p.open("a") as f:
        f.write(json.dumps(proposal) + "\n")


def write_team(pm_id: str, team: dict):
    p = _ws(pm_id) / "team.yaml"
    p.write_text(yaml.dump(team, default_flow_style=False, allow_unicode=True))


# ── Snapshot positions from DB ────────────────────────────────────────────────

def refresh_positions(pm_id: str, db_path: str = "paper_trades.db"):
    """Pull open positions from paper_trades.db and write to positions.json."""
    db = Path(db_path)
    if not db.exists():
        write_positions(pm_id, [])
        return
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM trades WHERE pm_id=? AND outcome='open'", (pm_id,)
        ).fetchall()
    write_positions(pm_id, [dict(r) for r in rows])


# ── Full context bundle for PM wakeup ─────────────────────────────────────────

def build_wakeup_context(pm_id: str, shift: str = "") -> str:
    """Return a formatted context string to prepend to a PM wakeup prompt."""
    inbox = drain_inbox(pm_id)
    positions = read_positions(pm_id)
    tasks = read_tasks(pm_id)
    plan = read_plan(pm_id)
    journal = read_journal(pm_id)
    summary = read_journal_summary(pm_id)

    lines = [
        f"# PM{pm_id} Wakeup Context",
        f"Shift: {shift}  |  Time: {datetime.now(IST).strftime('%Y-%m-%d %H:%M IST')}",
        "",
        "## Current Plan",
        plan or "(none)",
        "",
        "## Open Positions",
        json.dumps(positions, indent=2) if positions else "(none)",
        "",
        "## Tasks",
        yaml.dump(tasks, default_flow_style=False),
        "",
        "## Inbox Events",
        json.dumps(inbox, indent=2) if inbox else "(none)",
        "",
        "## Journal (last 7d)",
        journal or "(none)",
        "",
        "## Journal Summary (last 90d)",
        summary or "(none)",
    ]
    return "\n".join(lines)
