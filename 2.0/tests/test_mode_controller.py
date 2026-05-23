"""Boundary-case coverage for the mode controller.

Times below are constructed in IST so the test reads naturally. The controller
itself accepts any tz-aware datetime and converts internally; this is checked
in `test_utc_input_is_converted_to_ist`.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from agora.platform.control_plane.mode import (
    DEFAULT_CALENDAR,
    IST,
    HolidayCalendar,
    Mode,
    Override,
    compute_mode,
    current_mode,
)

UTC = ZoneInfo("UTC")

# A known weekday (Mon, Jan 5 2026) that is NOT in NSE_2026_HOLIDAYS.
TRADING_WEEKDAY: date = date(2026, 1, 5)
TRADING_SAT: date = date(2026, 1, 3)
TRADING_SUN: date = date(2026, 1, 4)
HOLIDAY_WEEKDAY: date = date(2026, 1, 26)  # Republic Day, Mon


def at(d: date, hh: int, mm: int, ss: int = 0) -> datetime:
    return datetime(d.year, d.month, d.day, hh, mm, ss, tzinfo=IST)


@pytest.mark.parametrize(
    ("when", "expected"),
    [
        (at(TRADING_WEEKDAY, 8, 59, 59), "build"),
        (at(TRADING_WEEKDAY, 9, 0, 0), "pre_trade_freeze"),
        (at(TRADING_WEEKDAY, 9, 14, 59), "pre_trade_freeze"),
        (at(TRADING_WEEKDAY, 9, 15, 0), "trading"),
        (at(TRADING_WEEKDAY, 13, 0, 0), "trading"),
        (at(TRADING_WEEKDAY, 15, 29, 59), "trading"),
        (at(TRADING_WEEKDAY, 15, 30, 0), "build"),
        (at(TRADING_WEEKDAY, 23, 59, 59), "build"),
    ],
)
def test_weekday_clock_boundaries(when: datetime, expected: Mode) -> None:
    assert current_mode(when, DEFAULT_CALENDAR, []) == expected


def test_saturday_is_build() -> None:
    assert current_mode(at(TRADING_SAT, 11, 0, 0), DEFAULT_CALENDAR, []) == "build"


def test_sunday_is_build() -> None:
    assert current_mode(at(TRADING_SUN, 11, 0, 0), DEFAULT_CALENDAR, []) == "build"


def test_holiday_weekday_is_build() -> None:
    # Mid-trading-hours but it's Republic Day → still build.
    assert current_mode(at(HOLIDAY_WEEKDAY, 13, 0, 0), DEFAULT_CALENDAR, []) == "build"


def test_override_trading_in_future_wins_over_clock() -> None:
    when = at(TRADING_SAT, 11, 0, 0)  # Saturday → would be build
    override = Override(mode="trading", expires_at=when + timedelta(hours=1))
    assert current_mode(when, DEFAULT_CALENDAR, [override]) == "trading"


def test_override_trading_in_past_is_ignored() -> None:
    when = at(TRADING_WEEKDAY, 8, 59, 59)  # Pre-open weekday → would be build
    expired = Override(mode="trading", expires_at=when - timedelta(seconds=1))
    assert current_mode(when, DEFAULT_CALENDAR, [expired]) == "build"


def test_override_overrides_holiday_to_trading() -> None:
    when = at(HOLIDAY_WEEKDAY, 11, 0, 0)
    override = Override(mode="trading", expires_at=when + timedelta(hours=1))
    assert current_mode(when, DEFAULT_CALENDAR, [override]) == "trading"


def test_naive_datetime_raises() -> None:
    naive = datetime(2026, 1, 5, 13, 0, 0)
    with pytest.raises(ValueError, match="tz-aware"):
        current_mode(naive, DEFAULT_CALENDAR, [])


def test_utc_input_is_converted_to_ist() -> None:
    # 03:45 UTC == 09:15 IST → trading.
    when_utc = datetime(2026, 1, 5, 3, 45, 0, tzinfo=UTC)
    assert current_mode(when_utc, DEFAULT_CALENDAR, []) == "trading"

    # 03:44:59 UTC == 09:14:59 IST → pre_trade_freeze.
    when_utc = datetime(2026, 1, 5, 3, 44, 59, tzinfo=UTC)
    assert current_mode(when_utc, DEFAULT_CALENDAR, []) == "pre_trade_freeze"


def test_holiday_calendar_membership() -> None:
    cal = HolidayCalendar([date(2026, 1, 1)])
    assert cal.is_trading_day(date(2026, 1, 1)) is False
    assert cal.is_trading_day(date(2026, 1, 2)) is True  # Friday, no holiday
    assert cal.is_trading_day(date(2026, 1, 3)) is False  # Saturday


def test_compute_mode_next_transition_during_trading_hours() -> None:
    when = at(TRADING_WEEKDAY, 13, 0, 0)
    result = compute_mode(when, DEFAULT_CALENDAR, [])
    assert result.mode == "trading"
    assert result.next_transition is not None
    nt_mode, nt_at = result.next_transition
    assert nt_mode == "build"
    assert nt_at == at(TRADING_WEEKDAY, 15, 30, 0)


def test_compute_mode_next_transition_during_freeze() -> None:
    when = at(TRADING_WEEKDAY, 9, 5, 0)
    result = compute_mode(when, DEFAULT_CALENDAR, [])
    assert result.mode == "pre_trade_freeze"
    assert result.next_transition is not None
    assert result.next_transition[0] == "trading"


def test_compute_mode_next_transition_before_open() -> None:
    when = at(TRADING_WEEKDAY, 8, 0, 0)
    result = compute_mode(when, DEFAULT_CALENDAR, [])
    assert result.mode == "build"
    assert result.next_transition is not None
    assert result.next_transition[0] == "pre_trade_freeze"


def test_compute_mode_after_close_no_today_transition() -> None:
    when = at(TRADING_WEEKDAY, 16, 0, 0)
    result = compute_mode(when, DEFAULT_CALENDAR, [])
    assert result.mode == "build"
    # K1: cross-day next_transition is intentionally not computed.
    assert result.next_transition is None


def test_compute_mode_with_override_reports_expiry() -> None:
    when = at(TRADING_SAT, 11, 0, 0)
    override = Override(mode="trading", expires_at=when + timedelta(hours=2))
    result = compute_mode(when, DEFAULT_CALENDAR, [override])
    assert result.mode == "trading"
    assert result.next_transition is not None
    nt_mode, nt_at = result.next_transition
    assert nt_at == when + timedelta(hours=2)
    assert nt_mode == "build"
