"""Journal append helper for PM workspaces.

K2 audit follow-up. The heartbeat activity (and the K3.5 trading-cycle
activity) both append lines to ``<workspace_root>/<pm_id>/journals/<UTC
date>.md``. Centralising the file path resolution + write keeps the two
sites honest about:

* the same UTC-bounded date for the filename — the dashboard reads
  *today's* journal and computes the date the same way, so a drift
  here invisibly hides entries across the midnight roll-over;
* idempotent ``mkdir(parents=True, exist_ok=True)`` before each write
  so an out-of-order operator who clears the workspace tree does not
  knock the heartbeat over;
* append-only ``open("a", encoding="utf-8")`` semantics — no
  retry-safe idempotency token, by design (heartbeats use
  ``maximum_attempts=1``; the trading cycle journals one line per
  decision, which is the natural unit of work).

Sandbox safety
--------------
This module imports only stdlib + ``agora.platform.control_plane.pm_provision``
(which itself imports ``yaml`` at module top — harmless because journal
writes only happen from activity bodies, never from workflow code). The
heartbeat activity already deferred its imports inside the function
body; that pattern is preserved by routing through this helper.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from agora.platform.control_plane.pm_provision import resolve_workspace_root


def journal_append(
    pm_id: str,
    line: str,
    *,
    workspace_root: Path | None = None,
    now: datetime | None = None,
) -> Path:
    """Append ``line`` to today's journal for ``pm_id``.

    Parameters
    ----------
    pm_id:
        The PM identifier; resolves to ``<workspace_root>/<pm_id>/journals``.
    line:
        The text to append. A trailing newline is added if the caller
        did not already include one. Callers should pre-format with the
        ``[<iso ts>] [<channel>]: <text>`` convention so the dashboard's
        journal viewer renders consistently.
    workspace_root:
        Override for tests; production callers leave this ``None`` so
        :func:`resolve_workspace_root` decides.
    now:
        Override for tests so the date filename is deterministic.
        Production callers leave ``None`` to use ``datetime.now(UTC)``.

    Returns
    -------
    Path
        The journal file written to. Returned for tests; production
        callers ignore.
    """
    when = now if now is not None else datetime.now(UTC)
    today = when.strftime("%Y-%m-%d")
    root = workspace_root if workspace_root is not None else resolve_workspace_root()
    journal_dir = root / pm_id / "journals"
    journal_dir.mkdir(parents=True, exist_ok=True)
    journal = journal_dir / f"{today}.md"
    text = line if line.endswith("\n") else line + "\n"
    with journal.open("a", encoding="utf-8") as fh:
        fh.write(text)
    return journal


__all__ = ["journal_append"]
