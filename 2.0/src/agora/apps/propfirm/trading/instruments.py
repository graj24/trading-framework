"""NSE equity instrument construction.

K3 Step 3.1. NautilusTrader does not ship a built-in NSE instrument
provider, so we mint our own. The shape follows
``TestInstrumentProvider.equity`` (Equity with ``instrument_id``,
``raw_symbol``, ``currency``, price/lot specs, and ``ts_event/ts_init``).

NSE-specific defaults:

* tick size ₹0.05 — the NSE standard tick for cash equities priced ≥ ₹1.
* lot size 1 — cash equities trade in single shares.
* currency INR — built-in to ``nautilus_trader.model.currencies``.
"""

from __future__ import annotations

from nautilus_trader.model.currencies import INR
from nautilus_trader.model.identifiers import InstrumentId, Symbol
from nautilus_trader.model.instruments import Equity
from nautilus_trader.model.objects import Price, Quantity

from agora.apps.propfirm.trading.engine import NSE_PAPER

#: NSE tick size for cash equities (₹0.05).
NSE_TICK_SIZE: str = "0.05"

#: NSE cash equities trade in lots of 1.
NSE_LOT_SIZE: int = 1

#: Symbols we have local parquet history for under
#: ``<repo-root>/stocks/<SYMBOL>/price_history.parquet`` (one level up
#: from the AGORA 2.0 tree). This is the K3 watchlist seed; K3.5+ will
#: expand it. The list is intentionally a subset of the full NIFTY 50.
NIFTY_50_SYMBOLS: list[str] = [
    "ETERNAL",
    "HDFCBANK",
    "INDIGO",
    "INFY",
    "RELIANCE",
    "SBIN",
    "TATACONSUM",
    "TCS",
    "TITAN",
    "VEDL",
]


def nse_equity(symbol: str) -> Equity:
    """Build an NSE-PAPER ``Equity`` instrument for ``symbol``.

    Parameters
    ----------
    symbol:
        Ticker, e.g. ``"RELIANCE"``. Case-sensitive: NautilusTrader's
        ``Symbol`` preserves the string verbatim, and our parquet files
        live under uppercase directories.

    Returns
    -------
    Equity
        An NSE-PAPER tagged equity with INR currency, ₹0.05 tick, lot
        size 1, and ``ts_event=ts_init=0`` (the conventional placeholder
        for instruments authored at engine boot, not derived from a
        market data event).
    """
    raw_symbol = Symbol(symbol)
    return Equity(
        instrument_id=InstrumentId(symbol=raw_symbol, venue=NSE_PAPER),
        raw_symbol=raw_symbol,
        currency=INR,
        price_precision=2,
        price_increment=Price.from_str(NSE_TICK_SIZE),
        lot_size=Quantity.from_int(NSE_LOT_SIZE),
        ts_event=0,
        ts_init=0,
    )
