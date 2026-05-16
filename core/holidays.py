"""NSE holiday calendar (offline, hand-curated).

Source: https://www.nseindia.com/resources/exchange-communication-holidays
Years covered: 2024–2026 (refresh annually before the new year — see
docs/analysis/06-improvements.md P2 §B11).

Used by `india_intraday_model._fo_expiry_days` to shift F&O expiry to the
prior trading day when the standard "last Thursday of month" lands on a
holiday.
"""
from __future__ import annotations

from datetime import date, timedelta

# Trading-day holidays (NSE cash + F&O). Half-days are NOT included
# because they're still trading days for expiry purposes.
NSE_HOLIDAYS: frozenset[date] = frozenset(
    {
        # 2024
        date(2024, 1, 26),  # Republic Day
        date(2024, 3, 8),   # Mahashivratri
        date(2024, 3, 25),  # Holi
        date(2024, 3, 29),  # Good Friday
        date(2024, 4, 11),  # Id-Ul-Fitr
        date(2024, 4, 17),  # Ram Navami
        date(2024, 5, 1),   # Maharashtra Day
        date(2024, 5, 20),  # Election (Mumbai)
        date(2024, 6, 17),  # Bakri Id
        date(2024, 7, 17),  # Muharram
        date(2024, 8, 15),  # Independence Day
        date(2024, 10, 2),  # Gandhi Jayanti
        date(2024, 11, 1),  # Diwali (Laxmi Puja — special muhurat session)
        date(2024, 11, 15), # Gurunanak Jayanti
        date(2024, 12, 25), # Christmas
        # 2025
        date(2025, 2, 26),  # Mahashivratri
        date(2025, 3, 14),  # Holi
        date(2025, 3, 31),  # Id-Ul-Fitr
        date(2025, 4, 10),  # Mahavir Jayanti
        date(2025, 4, 14),  # Ambedkar Jayanti
        date(2025, 4, 18),  # Good Friday
        date(2025, 5, 1),   # Maharashtra Day
        date(2025, 8, 15),  # Independence Day
        date(2025, 8, 27),  # Ganesh Chaturthi
        date(2025, 10, 2),  # Gandhi Jayanti / Dussehra
        date(2025, 10, 21), # Diwali Laxmi Puja
        date(2025, 10, 22), # Balipratipada
        date(2025, 11, 5),  # Gurunanak Jayanti
        date(2025, 12, 25), # Christmas
        # 2026
        date(2026, 1, 26),
        date(2026, 3, 6),
        date(2026, 3, 31),
        date(2026, 4, 3),
        date(2026, 4, 14),
        date(2026, 5, 1),
        date(2026, 8, 15),
        date(2026, 10, 2),
        date(2026, 11, 9),
        date(2026, 12, 25),
    }
)


def is_trading_day(d: date) -> bool:
    """True iff `d` is a Mon-Fri non-holiday (cash market trading day)."""
    if d.weekday() >= 5:  # Saturday(5), Sunday(6)
        return False
    return d not in NSE_HOLIDAYS


def previous_trading_day(d: date) -> date:
    """Walk backwards until we hit a trading day. Returns `d` itself if it
    is already a trading day."""
    cur = d
    while not is_trading_day(cur):
        cur -= timedelta(days=1)
    return cur
