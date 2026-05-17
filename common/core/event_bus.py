"""
SQLite-backed pub/sub event bus.

Topics (examples):
  price.spike.<SYMBOL>
  news.<SYMBOL>
  fill.<PM_ID>
  risk.breach.<PM_ID>
  pm.wakeup.<PM_ID>
  system.kill_switch

Usage:
  bus = EventBus()
  bus.publish("price.spike.RELIANCE", {"symbol": "RELIANCE", "pct": 2.1})
  for event in bus.subscribe("price.spike.*", since_id=last_id):
      ...
"""
from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator

DB_PATH = Path("events.db")


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH, check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c


def _ensure_schema():
    with _conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                topic     TEXT    NOT NULL,
                payload   TEXT    NOT NULL,
                pm_id     TEXT,
                severity  TEXT    DEFAULT 'INFO',
                ts        TEXT    NOT NULL
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_topic ON events(topic)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_ts    ON events(ts)")


_ensure_schema()


class EventBus:
    def publish(
        self,
        topic: str,
        payload: dict,
        pm_id: str | None = None,
        severity: str = "INFO",
    ) -> int:
        """Insert event, return its id."""
        with _conn() as c:
            cur = c.execute(
                "INSERT INTO events(topic, payload, pm_id, severity, ts) VALUES(?,?,?,?,?)",
                (topic, json.dumps(payload), pm_id, severity, datetime.utcnow().isoformat()),
            )
            return cur.lastrowid

    def subscribe(
        self,
        topic_pattern: str,
        since_id: int = 0,
        pm_id: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        """
        Fetch events matching topic_pattern (supports trailing *).
        Returns list of dicts sorted by id asc.
        """
        with _conn() as c:
            if topic_pattern.endswith("*"):
                prefix = topic_pattern[:-1]
                rows = c.execute(
                    "SELECT * FROM events WHERE topic LIKE ? AND id > ? ORDER BY id LIMIT ?",
                    (prefix + "%", since_id, limit),
                ).fetchall()
            else:
                rows = c.execute(
                    "SELECT * FROM events WHERE topic = ? AND id > ? ORDER BY id LIMIT ?",
                    (topic_pattern, since_id, limit),
                ).fetchall()

        result = []
        for r in rows:
            d = dict(r)
            d["payload"] = json.loads(d["payload"])
            result.append(d)
        return result

    def latest_id(self) -> int:
        with _conn() as c:
            row = c.execute("SELECT MAX(id) FROM events").fetchone()
            return row[0] or 0

    def poll(
        self,
        topic_pattern: str,
        since_id: int,
        pm_id: str | None = None,
        poll_interval: float = 1.0,
        timeout: float = 30.0,
    ) -> Iterator[dict]:
        """Block-poll until timeout, yielding events as they arrive."""
        deadline = time.time() + timeout
        cursor = since_id
        while time.time() < deadline:
            events = self.subscribe(topic_pattern, since_id=cursor, pm_id=pm_id)
            for ev in events:
                cursor = ev["id"]
                yield ev
            if not events:
                time.sleep(poll_interval)

    def prune(self, older_than_days: int = 7):
        """Delete old events to keep DB small."""
        with _conn() as c:
            c.execute(
                "DELETE FROM events WHERE ts < datetime('now', ?)",
                (f"-{older_than_days} days",),
            )


# Module-level singleton
_bus: EventBus | None = None


def get_bus() -> EventBus:
    global _bus
    if _bus is None:
        _bus = EventBus()
    return _bus
