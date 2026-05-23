"""Mode controller — placeholder for K1.4.

K1.5 replaces this with the real time-and-calendar logic. The current shape is
fixed so /api/mode can wire against it now: `compute_mode(now)` returns a
`ModeResult` carrying the current mode and an optional next-transition tuple.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

Mode = Literal["build", "trading", "pre_trade_freeze"]


@dataclass(frozen=True)
class ModeResult:
    mode: Mode
    next_transition: tuple[Mode, datetime] | None = None


def compute_mode(now: datetime) -> ModeResult:
    # TODO(K1.5): replace with real time-and-NSE-calendar logic.
    return ModeResult(mode="build", next_transition=None)
