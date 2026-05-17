"""Test for B.10 — F&O expiry shifts when the standard last-Thursday is a
holiday.

Concrete example: 2025-10-30 is the last Thursday of October 2025.
NSE_HOLIDAYS includes 2025-10-21 (Diwali), but October 30 is itself a
trading day, so for the test we just verify the helper correctly handles
the case when expiry falls on a holiday.

We construct a synthetic case: pretend the next holiday IS the last
Thursday — verify expiry shifts.
"""
from __future__ import annotations

from datetime import date

import pandas as pd


def test_holiday_calendar_basic():
    from core.holidays import is_trading_day, previous_trading_day, NSE_HOLIDAYS
    # Random known holiday.
    assert date(2025, 4, 18) in NSE_HOLIDAYS  # Good Friday 2025
    assert not is_trading_day(date(2025, 4, 18))
    # 2025-04-17 is a Thursday and not in the holiday set → trading day.
    assert is_trading_day(date(2025, 4, 17))
    # previous_trading_day backs off through holiday + weekend if needed.
    assert previous_trading_day(date(2025, 4, 18)) == date(2025, 4, 17)


def test_fo_expiry_uses_previous_trading_day_when_thursday_is_holiday(monkeypatch):
    """Inject a holiday that lands on a last-Thursday of the month, then
    confirm `_fo_expiry_days` shifts to the previous trading day.
    """
    from core import holidays
    from models.india_intraday_model import _fo_expiry_days

    # 2024-05-30 is the last Thursday of May 2024. Pretend it's a holiday.
    fake_holidays = frozenset(holidays.NSE_HOLIDAYS | {date(2024, 5, 30)})
    monkeypatch.setattr(holidays, "NSE_HOLIDAYS", fake_holidays)

    # Compute expiry distance for 2024-05-25 (a Saturday — not a trading
    # day either, but the function operates on any timestamp).
    idx = pd.DatetimeIndex([pd.Timestamp("2024-05-25 10:00:00")])
    days = int(_fo_expiry_days(idx).iloc[0])
    # Expiry shifts from 2024-05-30 (Thu) to 2024-05-29 (Wed).
    assert days == (date(2024, 5, 29) - date(2024, 5, 25)).days


def test_fo_expiry_unchanged_when_thursday_is_trading_day():
    """Sanity: when the last Thursday is a normal trading day, expiry
    stays on Thursday."""
    from models.india_intraday_model import _fo_expiry_days

    # 2025-08-28 — last Thursday of August 2025; not in NSE_HOLIDAYS.
    idx = pd.DatetimeIndex([pd.Timestamp("2025-08-25 10:00:00")])
    days = int(_fo_expiry_days(idx).iloc[0])
    assert days == (date(2025, 8, 28) - date(2025, 8, 25)).days
