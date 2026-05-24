"""End-of-day close for paper positions.

K3 Step 3.6. Once per trading day at 15:25 IST, close every open
paper position at the latest available close price. Outcome is
recorded as ``'eod_close'`` and the journal records each
closure / skip.

Pure async; the Temporal activity wrapper lives in
:mod:`agora.platform.workers.pm_supervisor`. This module has no
Temporal references — tests call it directly with a stubbed
:class:`agora.apps.propfirm.data.nse.MarketDataAdapter`.

Outcome vocabulary
------------------
Every successful close uses the literal ``'eod_close'`` from
:data:`agora.platform.control_plane.trade_repo.TradeOutcome`. Trades
the closer skipped (because market data was missing or the snapshot
raised) stay ``'open'`` — they get another shot next trading day.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

import asyncpg
from loguru import logger

from agora.platform.control_plane import trade_repo
from agora.platform.shared.journal import journal_append

if TYPE_CHECKING:
    from agora.apps.propfirm.data.nse import MarketDataAdapter


@dataclass(frozen=True)
class EodCloseResult:
    """Per-PM summary of the EOD close.

    ``closed`` is the list of trade ids the closer successfully
    moved to ``outcome='eod_close'``. ``skipped`` is per-symbol
    reasons the closer logged (no price data, snapshot raised,
    close-trade refused). Callers can render either as a "did the
    closer do anything?" indicator.
    """

    pm_id: str
    closed: list[int] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


def _journal_closed(
    pm_id: str,
    symbol: str,
    side: str,
    quantity: int,
    exit_price: Decimal,
    trade_id: int,
    pnl: Decimal | None,
) -> None:
    pnl_str = f" pnl={pnl}" if pnl is not None else ""
    journal_append(
        pm_id,
        f"[{datetime.now(UTC).isoformat()}] [eod]: "
        f"CLOSED {side} {quantity} {symbol} @ ₹{exit_price} "
        f"[trade_id={trade_id}]{pnl_str} (eod_close)",
    )


def _journal_skip(pm_id: str, symbol: str, trade_id: int, reason: str) -> None:
    journal_append(
        pm_id,
        f"[{datetime.now(UTC).isoformat()}] [eod]: "
        f"SKIPPED {symbol} [trade_id={trade_id}] ({reason})",
    )


def _journal_summary(
    pm_id: str,
    closed_count: int,
    skipped_count: int,
    total_pnl: Decimal,
) -> None:
    """Append the per-run rollup line.

    Plan §5 DoD #4 promised "a nightly summary lands in
    /pms/<pm>/journals/<date>.md". The per-trade ``CLOSED`` /
    ``SKIPPED`` lines satisfied the spirit, but an operator
    skimming the journal still had to count by hand. This is the
    one-line rollup. Always written, even when no trades were
    open, so an empty EOD pass is distinguishable from a missing
    one.
    """
    journal_append(
        pm_id,
        f"[{datetime.now(UTC).isoformat()}] [eod]: "
        f"SUMMARY closed={closed_count} skipped={skipped_count} "
        f"total_pnl=₹{total_pnl}",
    )


async def close_positions_for_pm(
    pool: asyncpg.Pool,
    pm_id: str,
    *,
    market_data: MarketDataAdapter | None = None,
) -> EodCloseResult:
    """Close every open trade for one PM at the latest available price.

    For each open trade:

    1. Look up the latest close price via
       ``market_data.snapshot([symbol])``.
    2. Call :func:`trade_repo.close_trade` with that price and
       ``outcome='eod_close'``.
    3. Append a journal line. Skipped trades are journaled with the
       reason so the operator can spot patterns.

    Skips (don't fail) trades whose symbol has no price data or
    whose adapter call raises — they remain ``open`` and the next
    EOD pass will retry. Other exceptions (DB outage, etc.) bubble
    up so Temporal's retry policy can decide.

    The default adapter is :class:`ParquetMarketData` (matches the
    K3.5 trading cycle). Tests pass a stub via ``market_data``.
    """
    if market_data is None:
        # Lazy import: the parquet adapter pulls in pandas/numpy. The
        # production caller (the activity body) already has them; tests
        # stub the adapter and don't need that import path.
        from agora.apps.propfirm.data.nse import ParquetMarketData

        market_data = ParquetMarketData()

    open_trades = await trade_repo.list_open_trades(pool, pm_id)
    result = EodCloseResult(pm_id=pm_id)
    total_pnl = Decimal(0)

    for trade in open_trades:
        symbol = trade.symbol
        try:
            quotes = await market_data.snapshot([symbol])
        except FileNotFoundError as e:
            reason = f"no market data: {e}"
            _journal_skip(pm_id, symbol, trade.id, reason)
            result.skipped.append(f"{symbol}: {reason}")
            continue
        except Exception as e:  # defensive — never let one symbol kill the EOD pass
            logger.warning(
                "eod_close: snapshot({}) failed for trade {}: {}",
                symbol,
                trade.id,
                e,
            )
            reason = f"market data error: {type(e).__name__}"
            _journal_skip(pm_id, symbol, trade.id, reason)
            result.skipped.append(f"{symbol}: {reason}")
            continue

        quote = quotes.get(symbol)
        if quote is None:
            reason = "snapshot returned no quote"
            _journal_skip(pm_id, symbol, trade.id, reason)
            result.skipped.append(f"{symbol}: {reason}")
            continue

        exit_price = Decimal(str(quote.price))
        try:
            closed = await trade_repo.close_trade(
                pool,
                trade.id,
                exit_price=exit_price,
                exit_ts=datetime.now(UTC),
                outcome="eod_close",
            )
        except ValueError as e:
            # Already-closed (race with the trading cycle) or missing.
            # Either way, journal and move on; the closer doesn't own
            # the truth — the trade row does.
            reason = f"close_trade refused: {e}"
            _journal_skip(pm_id, symbol, trade.id, reason)
            result.skipped.append(f"{symbol}: {reason}")
            continue

        _journal_closed(
            pm_id,
            symbol,
            trade.side,
            trade.quantity,
            exit_price,
            trade.id,
            closed.pnl_inr,
        )
        result.closed.append(trade.id)
        if closed.pnl_inr is not None:
            total_pnl += closed.pnl_inr

    # Always write the rollup, even when ``open_trades`` was empty —
    # the dashboard / operator uses its presence to confirm the EOD
    # workflow ran at all (vs failing before it could write anything).
    _journal_summary(pm_id, len(result.closed), len(result.skipped), total_pnl)

    return result


__all__ = ["EodCloseResult", "close_positions_for_pm"]
