"""Mode controller — pure-Python decision logic for AGORA's three operating modes.

The controller decides which of three modes the platform is in at any given
instant:

  build               — agents may reason, write code, evolve strategies.
  pre_trade_freeze    — short window before market open; no plan changes,
                        no order submission, just final preparation.
  trading             — orders may be submitted to the broker.

The decision graph (per plan/01-KEYSTONE.md §3 Step 1.5 + §7.2):

  1. If an active override exists (expires_at > now), its mode wins.
     For K1, "active" means the most-recently-requested unexpired override;
     a richer precedence model can come later.
  2. If `now` is not a trading day in IST (weekend or NSE holiday) → build.
  3. Trading day, IST clock:
       t < 09:00              → build
       09:00 <= t < 09:15     → pre_trade_freeze
       09:15 <= t < 15:30     → trading
       t >= 15:30             → build

The function takes a tz-aware datetime and converts internally to IST. Naive
datetimes raise ValueError — silently assuming UTC is a footgun.

Holiday calendar for NSE 2026 is hard-coded below. The dates marked with
"verify" in the comment are best-effort and need confirmation against NSE's
published 2026 trading-holiday schedule before live trading. K1 is paper /
infra only, so wrong-but-flagged is acceptable here.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Literal
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

Mode = Literal["build", "trading", "pre_trade_freeze"]

MARKET_OPEN = time(9, 0, 0)
PRE_TRADE_FREEZE_END = time(9, 15, 0)
MARKET_CLOSE = time(15, 30, 0)


# NSE 2026 holidays — derived from the published NSE 2026 trading-holiday circular.
# TODO: cross-check against the official PDF when it is final; lunar/festival dates
# (Eid, Bakri Eid, Ganesh Chaturthi) may shift by a day depending on moon-sighting.
NSE_2026_HOLIDAYS: frozenset[date] = frozenset(
    {
        date(2026, 1, 26),  # Republic Day                 (Mon)
        date(2026, 2, 19),  # Mahashivratri                (Thu)
        date(2026, 3, 3),  # Holi                          (Tue)
        date(2026, 3, 20),  # Eid-ul-Fitr (date may shift) (Fri)
        date(2026, 4, 1),  # Mahavir Jayanti               (Wed)
        date(2026, 4, 3),  # Good Friday                   (Fri)
        date(2026, 4, 14),  # Ambedkar Jayanti             (Tue)
        date(2026, 5, 1),  # Maharashtra Day               (Fri)
        date(2026, 5, 27),  # Buddha Pournima              (Wed)
        date(2026, 6, 16),  # Bakri Eid (date may shift)   (Tue)
        date(2026, 8, 15),  # Independence Day             (Sat — observed?)
        date(2026, 8, 26),  # Ganesh Chaturthi (may shift) (Wed)
        date(2026, 10, 2),  # Gandhi Jayanti               (Fri)
        date(2026, 10, 21),  # Diwali Laxmi Pujan / Muhurat (Wed)
        date(2026, 10, 22),  # Diwali-Balipratipada        (Thu)
        date(2026, 11, 24),  # Guru Nanak Jayanti          (Tue)
        date(2026, 12, 25),  # Christmas                    (Fri)
    }
)


class HolidayCalendar:
    """Holiday calendar wrapper. Keeps the open/closed test on one object so
    tests and future calendars (NSE 2027, BSE, ...) can drop in by passing a
    different `holidays` set."""

    def __init__(self, holidays: Iterable[date]) -> None:
        self._holidays: frozenset[date] = frozenset(holidays)

    def is_trading_day(self, d: date) -> bool:
        # Mon=0..Sun=6. NSE trades Mon-Fri minus listed holidays.
        if d.weekday() >= 5:
            return False
        return d not in self._holidays

    @property
    def holidays(self) -> frozenset[date]:
        return self._holidays


DEFAULT_CALENDAR = HolidayCalendar(NSE_2026_HOLIDAYS)


@dataclass(frozen=True)
class Override:
    """A mode override. `expires_at` must be tz-aware."""

    mode: Mode
    expires_at: datetime
    requested_at: datetime | None = None  # used for "most recent wins" tiebreak


@dataclass(frozen=True)
class ModeResult:
    """Return shape of compute_mode / current_mode."""

    mode: Mode
    next_transition: tuple[Mode, datetime] | None = None


def _select_active_override(now: datetime, overrides: list[Override]) -> Override | None:
    """Pick the active override, if any.

    K1 precedence: among overrides whose expires_at > now, the most recently
    requested (or last in the list as a fallback) wins. Documented limitation:
    a richer model (priority levels, exclusive zones) can ship later without
    changing the call site.
    """
    active = [o for o in overrides if o.expires_at > now]
    if not active:
        return None

    def _key(o: Override) -> datetime:
        if o.requested_at is not None:
            return o.requested_at
        # Fallback so sort is stable when requested_at is omitted.
        return datetime.min.replace(tzinfo=UTC_FALLBACK)

    active.sort(key=_key)
    return active[-1]


# Sentinel used only as a tiebreaker key when requested_at isn't supplied.
UTC_FALLBACK = ZoneInfo("UTC")


def current_mode(
    now: datetime,
    calendar: HolidayCalendar,
    overrides: list[Override],
) -> Mode:
    """Decide the current mode. See module docstring for the decision graph."""
    if now.tzinfo is None:
        raise ValueError("current_mode() requires a tz-aware datetime")

    override = _select_active_override(now, overrides)
    if override is not None:
        return override.mode

    now_ist = now.astimezone(IST)
    if not calendar.is_trading_day(now_ist.date()):
        return "build"

    t = now_ist.time()
    if t < MARKET_OPEN:
        return "build"
    if t < PRE_TRADE_FREEZE_END:
        return "pre_trade_freeze"
    if t < MARKET_CLOSE:
        return "trading"
    return "build"


def _next_transition_today(now_ist: datetime) -> tuple[Mode, datetime] | None:
    """If we're inside trading hours today, name the next clock-driven flip."""
    today = now_ist.date()
    t = now_ist.time()
    open_dt = datetime.combine(today, MARKET_OPEN, tzinfo=IST)
    freeze_end_dt = datetime.combine(today, PRE_TRADE_FREEZE_END, tzinfo=IST)
    close_dt = datetime.combine(today, MARKET_CLOSE, tzinfo=IST)

    if t < MARKET_OPEN:
        return "pre_trade_freeze", open_dt
    if t < PRE_TRADE_FREEZE_END:
        return "trading", freeze_end_dt
    if t < MARKET_CLOSE:
        return "build", close_dt
    return None  # Past close; next transition is tomorrow's open, computed lazily.


def compute_mode(
    now: datetime,
    calendar: HolidayCalendar | None = None,
    overrides: list[Override] | None = None,
) -> ModeResult:
    """Convenience wrapper used by /api/mode. Returns mode + next transition.

    `next_transition` is best-effort: it's only filled in when the answer is
    cheap (today, trading day, before close). Cross-day computation (next
    trading day's open after a holiday) is deferred — see TODO below.
    """
    if calendar is None:
        calendar = DEFAULT_CALENDAR
    if overrides is None:
        overrides = []

    mode = current_mode(now, calendar, overrides)

    # Override active? Next transition = override expiry.
    override = _select_active_override(now, overrides)
    if override is not None:
        # When an override expires, fall back to the clock-driven mode.
        # Computing exactly what that will be at expiry requires recursing;
        # for K1 we just name "build" as the post-override mode unless we can
        # cheaply determine otherwise.
        return ModeResult(mode=mode, next_transition=("build", override.expires_at))

    if not calendar.is_trading_day(now.astimezone(IST).date()):
        # TODO: next trading day's open. Skipped for K1.
        return ModeResult(mode=mode, next_transition=None)

    nt = _next_transition_today(now.astimezone(IST))
    return ModeResult(mode=mode, next_transition=nt)
