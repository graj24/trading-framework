"""Tests for the journal-append helper extracted in K3 Step 3.5.

The K2 heartbeat activity used to inline its journal write; the K3
trading cycle journals on every placement / skip / rejection, and
duplicating the path-resolution + mkdir + write logic across two sites
was the K2 audit's "lingering recommendation". This file pins the
contract on the helper directly:

* Append goes to ``<root>/<pm_id>/journals/<UTC date>.md``.
* The directory is created if missing.
* The filename uses the UTC date — the dashboard's ``read_journal_tail``
  uses the same boundary, so the helper and the reader must agree
  across the midnight roll-over.
* A trailing newline is added if not present (caller can include their
  own).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from agora.platform.shared.journal import journal_append


def test_journal_append_writes_line(tmp_path: Path) -> None:
    """One append produces one trailing-newline line in today's file."""
    now = datetime(2025, 6, 15, 9, 0, tzinfo=UTC)
    written = journal_append(
        "pm1",
        "[2025-06-15T09:00:00+00:00] [trading]: PLACED LONG 10 RELIANCE",
        workspace_root=tmp_path,
        now=now,
    )
    assert written == tmp_path / "pm1" / "journals" / "2025-06-15.md"
    contents = written.read_text(encoding="utf-8")
    assert contents == ("[2025-06-15T09:00:00+00:00] [trading]: PLACED LONG 10 RELIANCE\n")


def test_journal_append_creates_dir_if_missing(tmp_path: Path) -> None:
    """``mkdir(parents=True, exist_ok=True)`` runs on every call.

    The provision activity creates the journal dir at PM spawn, but an
    out-of-order operator who clears the workspace must not knock the
    journal write over.
    """
    pm_dir = tmp_path / "pm1"
    assert not pm_dir.exists()
    written = journal_append(
        "pm1",
        "hello",
        workspace_root=tmp_path,
        now=datetime(2025, 1, 2, tzinfo=UTC),
    )
    assert written.parent == tmp_path / "pm1" / "journals"
    assert written.parent.is_dir()
    assert written.read_text(encoding="utf-8") == "hello\n"


def test_journal_append_appends_not_overwrites(tmp_path: Path) -> None:
    """Subsequent appends accumulate; nothing is clobbered."""
    now = datetime(2025, 1, 1, tzinfo=UTC)
    journal_append("pm1", "first", workspace_root=tmp_path, now=now)
    journal_append("pm1", "second\n", workspace_root=tmp_path, now=now)
    journal_append("pm1", "third", workspace_root=tmp_path, now=now)
    out = (tmp_path / "pm1" / "journals" / "2025-01-01.md").read_text(encoding="utf-8")
    assert out == "first\nsecond\nthird\n"


def test_journal_append_today_filename_in_utc(tmp_path: Path) -> None:
    """Filename uses UTC date, regardless of caller TZ.

    A request at 23:30 PST (07:30 UTC next day) lands in the next-day
    file. This is what the dashboard's ``read_journal_tail`` expects.
    """
    # 2025-06-15 23:30 PST = 2025-06-16 07:30 UTC. We pass a UTC-aware
    # datetime to the helper so the helper's ``strftime("%Y-%m-%d")``
    # produces the next-day date deterministically.
    utc_next_day = datetime(2025, 6, 16, 7, 30, tzinfo=UTC)
    written = journal_append(
        "pm1",
        "boundary",
        workspace_root=tmp_path,
        now=utc_next_day,
    )
    assert written.name == "2025-06-16.md"


def test_journal_append_per_pm_isolation(tmp_path: Path) -> None:
    """Two PMs writing the same day must not cross-pollinate."""
    now = datetime(2025, 5, 1, tzinfo=UTC)
    journal_append("pm1", "from pm1", workspace_root=tmp_path, now=now)
    journal_append("pm2", "from pm2", workspace_root=tmp_path, now=now)

    pm1_file = tmp_path / "pm1" / "journals" / "2025-05-01.md"
    pm2_file = tmp_path / "pm2" / "journals" / "2025-05-01.md"
    assert pm1_file.read_text(encoding="utf-8") == "from pm1\n"
    assert pm2_file.read_text(encoding="utf-8") == "from pm2\n"
