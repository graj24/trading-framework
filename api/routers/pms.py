"""PM monitoring API — state, audit, triage log, events."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse

from core.pm_runtime import list_pms, get_pm_config
from core.pm_state import (
    read_plan, read_tasks, read_journal, read_journal_summary,
    read_inbox, read_positions, read_proposals, read_team,
)
from core.broker import is_kill_switch_active, activate_kill_switch, deactivate_kill_switch

router = APIRouter(prefix="/api/pms", tags=["pms"])

BASE = Path(".")
AUDIT_LOG = BASE / "risk_audit.jsonl"
EVENTS_DB = BASE / "events.db"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _pm_summary(pm: dict) -> dict:
    pm_id = pm["pm_id"]
    cfg = get_pm_config(pm_id)
    positions = read_positions(pm_id)
    inbox = read_inbox(pm_id)

    # P&L from DB
    daily_pnl = 0.0
    db = BASE / "paper_trades.db"
    if db.exists():
        with sqlite3.connect(db) as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(pnl_inr),0) FROM trades "
                "WHERE pm_id=? AND outcome!='open' AND exit_date >= date('now')",
                (pm_id,),
            ).fetchone()
            daily_pnl = row[0] if row else 0.0

    # Last wakeup from events.db
    last_wakeup = None
    if EVENTS_DB.exists():
        with sqlite3.connect(EVENTS_DB) as conn:
            row = conn.execute(
                "SELECT ts FROM events WHERE topic=? ORDER BY id DESC LIMIT 1",
                (f"pm.wakeup.{pm_id}",),
            ).fetchone()
            last_wakeup = row[0] if row else None

    # Daemon status from events.db
    daemons = {}
    if EVENTS_DB.exists():
        with sqlite3.connect(EVENTS_DB) as conn:
            for daemon in ["triage", "trader", "risk"]:
                row = conn.execute(
                    "SELECT ts, payload FROM events WHERE topic=? ORDER BY id DESC LIMIT 1",
                    (f"system.daemon.{pm_id}",),
                ).fetchone()
                if row:
                    try:
                        p = json.loads(row[1])
                        if p.get("daemon") == daemon:
                            daemons[daemon] = {"ts": row[0], "event": p.get("event")}
                    except Exception:
                        pass

    return {
        **pm,
        "daily_pnl_inr": round(daily_pnl, 2),
        "open_positions": len(positions),
        "inbox_count": len(inbox),
        "last_wakeup": last_wakeup,
        "daemons": daemons,
        "capital": cfg.get("capital", 10000),
    }


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("")
def get_pms():
    pms = list_pms(active_only=False)
    return [_pm_summary(p) for p in pms]


@router.get("/{pm_id}/state")
def get_pm_state(pm_id: str):
    return {
        "pm_id": pm_id,
        "plan": read_plan(pm_id),
        "tasks": read_tasks(pm_id),
        "journal": read_journal(pm_id),
        "journal_summary": read_journal_summary(pm_id),
        "inbox": read_inbox(pm_id),
        "positions": read_positions(pm_id),
        "proposals": read_proposals(pm_id),
        "team": read_team(pm_id),
    }


@router.get("/{pm_id}/audit")
def get_pm_audit(pm_id: str, limit: int = Query(100, le=1000)):
    if not AUDIT_LOG.exists():
        return []
    entries = []
    for line in AUDIT_LOG.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            e = json.loads(line)
            if e.get("pm_id") == pm_id:
                entries.append(e)
        except Exception:
            pass
    return entries[-limit:]


@router.get("/{pm_id}/triage_log")
def get_triage_log(pm_id: str, limit: int = Query(100, le=1000)):
    log_path = BASE / f"logs/pm{pm_id}_triage_decisions.jsonl"
    if not log_path.exists():
        return []
    entries = []
    for line in log_path.read_text().splitlines():
        line = line.strip()
        if line:
            try:
                entries.append(json.loads(line))
            except Exception:
                pass
    return entries[-limit:]


@router.get("/{pm_id}/trades")
def get_pm_trades(pm_id: str, limit: int = Query(50, le=500)):
    db = BASE / "paper_trades.db"
    if not db.exists():
        return []
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM trades WHERE pm_id=? ORDER BY rowid DESC LIMIT ?",
            (pm_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]


@router.get("/events")
def get_events(
    since_id: int = Query(0),
    pm_id: str | None = Query(None),
    topic: str | None = Query(None),
    limit: int = Query(200, le=1000),
):
    """REST fallback for event bus — returns events since given id."""
    if not EVENTS_DB.exists():
        return []
    with sqlite3.connect(EVENTS_DB) as conn:
        conn.row_factory = sqlite3.Row
        if pm_id and topic:
            rows = conn.execute(
                "SELECT * FROM events WHERE id>? AND pm_id=? AND topic LIKE ? ORDER BY id LIMIT ?",
                (since_id, pm_id, topic.replace("*", "%"), limit),
            ).fetchall()
        elif pm_id:
            rows = conn.execute(
                "SELECT * FROM events WHERE id>? AND pm_id=? ORDER BY id LIMIT ?",
                (since_id, pm_id, limit),
            ).fetchall()
        elif topic:
            rows = conn.execute(
                "SELECT * FROM events WHERE id>? AND topic LIKE ? ORDER BY id LIMIT ?",
                (since_id, topic.replace("*", "%"), limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM events WHERE id>? ORDER BY id LIMIT ?",
                (since_id, limit),
            ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        try:
            d["payload"] = json.loads(d["payload"])
        except Exception:
            pass
        result.append(d)
    return result


@router.get("/events/latest_id")
def get_latest_event_id():
    if not EVENTS_DB.exists():
        return {"latest_id": 0}
    with sqlite3.connect(EVENTS_DB) as conn:
        row = conn.execute("SELECT MAX(id) FROM events").fetchone()
        return {"latest_id": row[0] or 0}


# ── Per-PM pause / resume ─────────────────────────────────────────────────────

def _pm_pause_path(pm_id: str) -> Path:
    return BASE / f"pm_{pm_id}" / "state" / "PAUSED"


@router.post("/{pm_id}/pause")
def pause_pm(pm_id: str, reason: str = "manual via UI"):
    """
    Pause a PM: writes a PAUSED sentinel file.
    - PM Trader daemon checks this before placing any order.
    - PM Triage daemon stops routing exec_order events.
    - Heartbeat scheduler skips wakeup issues for this PM.
    """
    p = _pm_pause_path(pm_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(reason)
    # Publish event so daemons react immediately
    try:
        from core.event_bus import get_bus
        get_bus().publish(f"system.pm.{pm_id}", {"event": "paused", "reason": reason}, pm_id=pm_id, severity="INFO")
    except Exception:
        pass
    return {"pm_id": pm_id, "paused": True, "reason": reason}


@router.post("/{pm_id}/resume")
def resume_pm(pm_id: str):
    """Remove the PAUSED sentinel — PM resumes normal operation."""
    p = _pm_pause_path(pm_id)
    if p.exists():
        p.unlink()
    try:
        from core.event_bus import get_bus
        get_bus().publish(f"system.pm.{pm_id}", {"event": "resumed"}, pm_id=pm_id, severity="INFO")
    except Exception:
        pass
    return {"pm_id": pm_id, "paused": False}


@router.get("/{pm_id}/paused")
def pm_paused_status(pm_id: str):
    p = _pm_pause_path(pm_id)
    return {"pm_id": pm_id, "paused": p.exists(), "reason": p.read_text() if p.exists() else ""}


# ── Kill switch ───────────────────────────────────────────────────────────────

@router.get("/kill_switch")
def kill_switch_status():
    active = is_kill_switch_active()
    reason = ""
    if active:
        p = Path("KILL_SWITCH")
        reason = p.read_text() if p.exists() else ""
    return {"active": active, "reason": reason}


@router.post("/kill_switch/activate")
def kill_switch_on(reason: str = "manual via UI"):
    activate_kill_switch(reason)
    return {"active": True, "reason": reason}


@router.post("/kill_switch/deactivate")
def kill_switch_off():
    deactivate_kill_switch()
    return {"active": False}
