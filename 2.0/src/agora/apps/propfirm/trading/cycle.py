"""K3 Step 3.5 trading cycle.

Orchestrates one tick of the trading loop:

    load market data -> compute signal -> place order or skip / exit.

Called by the ``trading_cycle`` activity in
:mod:`agora.platform.workers.pm_supervisor`. Pure async; no Temporal
references and no workflow-side imports — tests can call this
directly with mocked dependencies.

K3 contract: one cycle == one bar's worth of work per symbol on the
PM's watchlist. Multi-symbol scans, position-level intra-bar SL
monitoring, and richer outcome vocabularies are deferred to K3.6+.

Design drift from plan/01-KEYSTONE.md §5 Step 3.5
--------------------------------------------------
The plan's pseudocode invokes ``strategy.generate_signals(market)``
synchronously, suggesting a NautilusTrader strategy runs per cycle.
NautilusTrader strategies are event-driven (``on_bar`` handler) — to
get one bar through, we'd boot a full ``BacktestEngine``, which is
slow per cycle and architecturally awkward (NT's ``submit_order`` goes
to NT's internal book, not AGORA's broker). Instead this module
computes the signal directly via :mod:`agora.apps.propfirm.seed_strategies.signals`
(SMA20/SMA50 + ATR14, the same math momentum_v1 uses) and submits via
AGORA's broker. The full NautilusTrader engine remains in use for the
trading smoke (3.1) and the backtest harness (3.3).

Outcome vocabulary
------------------
Signal-reversal exits land in :data:`TradeOutcome` ``'signal_exit'``
(added in the K3 post-audit pass). Operator-initiated closes use
``'manual'``; mixing the two would muddy K4+ exit-reason analytics.
The literal set is enforced in Python only — the ``outcome`` column
is plain TEXT in Postgres (migration ``0003``), so adding a value is a
one-line change with no migration.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING

import asyncpg
from loguru import logger

from agora.apps.propfirm.seed_strategies.signals import (
    MomentumSignal,
    compute_momentum_signal,
)
from agora.apps.propfirm.trading.instruments import NIFTY_50_SYMBOLS
from agora.platform.control_plane import pm_provision, pm_repo, trade_repo
from agora.platform.shared.journal import journal_append
from agora.platform.tools import broker
from agora.platform.tools.broker import BrokerError, OrderRequest

if TYPE_CHECKING:
    from agora.apps.propfirm.data.nse import MarketDataAdapter

#: Per-position capital fraction. Matches momentum_v1's
#: ``MomentumV1Config.pct_per_position`` (0.05 / 5%). K3 simplification:
#: the cycle does not enforce ``max_positions`` — that's a portfolio-
#: level allocator concern (deferred per momentum_v1 module docstring).
DEFAULT_POSITION_FRACTION = Decimal("0.05")

#: How many trailing bars the signal evaluator wants. We need at least
#: ``max(slow_period, atr_period + 1) = 50`` bars; 60 gives a small
#: cushion in case some symbols are short of history at the head.
DEFAULT_BAR_WINDOW = 60


@dataclass(frozen=True)
class CycleResult:
    """Per-cycle summary for the supervisor / dashboard.

    All four lists carry symbols (or, in ``placed`` / ``closed``, trade
    ids alongside the symbol). Callers can render any one of them as a
    "did the cycle do anything?" indicator.
    """

    pm_id: str
    placed: list[int] = field(default_factory=list)
    closed: list[int] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    rejected: list[str] = field(default_factory=list)


def _resolve_watchlist(
    pm: pm_repo.PMRecord,
    workspace_root: Path,
    explicit: list[str] | None,
) -> list[str]:
    """Pick the cycle's watchlist.

    Precedence (highest first):
      1. Explicit ``watchlist`` argument from the caller (test override).
      2. ``watchlist`` key in the PM's ``config.yaml``.
      3. :data:`agora.apps.propfirm.trading.instruments.NIFTY_50_SYMBOLS`.
    """
    if explicit:
        return list(explicit)
    workspace = workspace_root / pm.id
    cfg = pm_provision.load_pm_config(workspace)
    raw = cfg.get("watchlist")
    if isinstance(raw, list) and all(isinstance(s, str) for s in raw):
        return list(raw)
    return list(NIFTY_50_SYMBOLS)


def _bars_to_floats(bars: list[object]) -> tuple[list[float], list[float], list[float]]:
    """Translate a list of NautilusTrader ``Bar`` objects into closes/highs/lows.

    Bars are typed as ``object`` here so the cycle module can be
    imported without forcing :mod:`nautilus_trader.model.data` into
    the import graph (matters because tests stub the adapter and may
    pass plain dataclasses in place of ``Bar``). The ``as_double()``
    duck-type is the contract.
    """
    closes: list[float] = []
    highs: list[float] = []
    lows: list[float] = []
    for bar in bars:
        # ``Bar.close`` etc. return ``Price``; ``Price.as_double()`` is
        # the canonical float coercion. The Decimal path
        # (``as_decimal()``) is intentionally avoided here — the signal
        # math is float-based, and we re-promote to Decimal at the
        # broker boundary via ``Decimal(str(x))``.
        closes.append(bar.close.as_double())  # type: ignore[attr-defined]
        highs.append(bar.high.as_double())  # type: ignore[attr-defined]
        lows.append(bar.low.as_double())  # type: ignore[attr-defined]
    return closes, highs, lows


def _quantity_for(starting_capital_inr: float, price: Decimal) -> int:
    """K3 sizing: 5% of starting capital, floor-rounded to whole shares.

    Returns ``0`` when one share is too expensive to fit in the budget;
    the cycle treats that as a SKIP for sizing reasons (logged via the
    rejection journal so an operator notices).
    """
    if price <= 0:
        return 0
    budget = Decimal(str(starting_capital_inr)) * DEFAULT_POSITION_FRACTION
    qty = int(budget // price)
    return max(qty, 0)


def _journal_signal_skip(pm_id: str, signal: MomentumSignal) -> None:
    journal_append(
        pm_id,
        f"[{datetime.now(UTC).isoformat()}] [trading]: "
        f"SKIPPED {signal.symbol} ({signal.rationale})",
    )


def _journal_skip(pm_id: str, symbol: str, reason: str) -> None:
    journal_append(
        pm_id,
        f"[{datetime.now(UTC).isoformat()}] [trading]: SKIPPED {symbol} ({reason})",
    )


def _journal_reject(pm_id: str, symbol: str, reason: str) -> None:
    journal_append(
        pm_id,
        f"[{datetime.now(UTC).isoformat()}] [trading]: REJECTED {symbol}: {reason}",
    )


def _journal_placed(pm_id: str, signal: MomentumSignal, qty: int, trade_id: int) -> None:
    journal_append(
        pm_id,
        f"[{datetime.now(UTC).isoformat()}] [trading]: "
        f"PLACED LONG {qty} {signal.symbol} @ ₹{signal.price} "
        f"[trade_id={trade_id}] ({signal.rationale})",
    )


def _journal_closed(
    pm_id: str,
    symbol: str,
    side: str,
    qty: int,
    exit_price: Decimal,
    trade_id: int,
    pnl: Decimal | None,
    rationale: str,
) -> None:
    pnl_str = f" pnl={pnl}" if pnl is not None else ""
    journal_append(
        pm_id,
        f"[{datetime.now(UTC).isoformat()}] [trading]: "
        f"CLOSED {side} {qty} {symbol} @ ₹{exit_price} "
        f"[trade_id={trade_id}]{pnl_str} ({rationale})",
    )


async def run_trading_cycle(
    pool: asyncpg.Pool,
    pm_id: str,
    *,
    watchlist: list[str] | None = None,
    market_data: MarketDataAdapter | None = None,
    bar_window: int = DEFAULT_BAR_WINDOW,
) -> CycleResult:
    """Run one trading cycle for ``pm_id``.

    Loads the PM record, resolves the watchlist, computes a momentum
    signal per symbol against the latest ``bar_window`` bars, and
    either places (LONG), closes (EXIT), or skips. Each outcome lands
    in the PM's journal so the dashboard ticker shows what happened.

    Returns a :class:`CycleResult` summarising the cycle. Broker
    rejections are caught and recorded as ``rejected`` entries — they
    do not bubble out of this function. Other exceptions (DB,
    market-data) propagate and Temporal's retry policy handles them
    via the activity wrapper.
    """
    pm = await pm_repo.get_pm(pool, pm_id)
    if pm is None:
        _journal_reject(pm_id, "*", "pm not found")
        return CycleResult(pm_id=pm_id, rejected=[f"pm {pm_id!r} not found"])
    if pm.status != "running":
        msg = f"pm not running (status={pm.status})"
        _journal_reject(pm_id, "*", msg)
        return CycleResult(pm_id=pm_id, rejected=[msg])

    workspace_root = pm_provision.resolve_workspace_root()
    symbols = _resolve_watchlist(pm, workspace_root, watchlist)

    if market_data is None:
        # Lazy import: the parquet adapter pulls in pandas/numpy. The
        # production caller (the activity body) already has them, but
        # tests stub the adapter and don't need that import path.
        from agora.apps.propfirm.data.nse import ParquetMarketData

        market_data = ParquetMarketData()

    open_trades = await trade_repo.list_open_trades(pool, pm_id)
    open_by_symbol: dict[str, trade_repo.PaperTradeRecord] = {t.symbol: t for t in open_trades}

    result = CycleResult(pm_id=pm_id)

    for symbol in symbols:
        try:
            bars = await market_data.bars(symbol, n=bar_window)
        except FileNotFoundError as e:
            _journal_skip(pm_id, symbol, f"no market data: {e}")
            result.skipped.append(symbol)
            continue
        except Exception as e:  # defensive — never let one symbol kill the cycle
            logger.warning("trading_cycle: bars({}) failed: {}", symbol, e)
            _journal_skip(pm_id, symbol, f"market data error: {type(e).__name__}")
            result.skipped.append(symbol)
            continue

        if not bars:
            _journal_skip(pm_id, symbol, "no bars returned")
            result.skipped.append(symbol)
            continue

        closes, highs, lows = _bars_to_floats(bars)
        existing = open_by_symbol.get(symbol)
        is_in_position = existing is not None

        signal = compute_momentum_signal(
            symbol,
            closes,
            highs,
            lows,
            is_in_position=is_in_position,
        )

        if signal.kind == "NONE":
            _journal_signal_skip(pm_id, signal)
            result.skipped.append(symbol)
            continue

        if signal.kind == "LONG":
            qty = _quantity_for(pm.starting_capital_inr, signal.price)
            if qty < 1:
                msg = f"insufficient capital for >=1 share at " f"₹{signal.price} (5% budget)"
                _journal_reject(pm_id, symbol, msg)
                result.rejected.append(f"{symbol}: {msg}")
                continue
            order = OrderRequest(
                pm_id=pm_id,
                symbol=symbol,
                side="LONG",
                quantity=qty,
                entry_price=signal.price,
                stop_loss=signal.stop_loss,
                strategy_id="momentum_v1",
                metadata={"rationale": signal.rationale},
            )
            try:
                placed = await broker.submit_order(pool, order)
            except BrokerError as e:
                _journal_reject(pm_id, symbol, str(e))
                result.rejected.append(f"{symbol}: {e}")
                continue
            _journal_placed(pm_id, signal, qty, placed.trade_id)
            result.placed.append(placed.trade_id)
            continue

        # signal.kind == "EXIT"
        # ``existing`` is non-None when ``is_in_position`` was True
        # (which is the precondition for an EXIT signal). Defensive
        # check for the pathological case where the open-trades query
        # races a parallel close.
        if existing is None:
            _journal_skip(pm_id, symbol, "exit signal but no open trade found")
            result.skipped.append(symbol)
            continue
        try:
            closed = await trade_repo.close_trade(
                pool,
                existing.id,
                exit_price=signal.price,
                exit_ts=datetime.now(UTC),
                # 'signal_exit' bucket (K3 post-audit). Distinct from
                # operator-initiated 'manual' closes so K4+ PM
                # reasoning over exit reasons isn't ambiguous. See
                # module docstring + trade_repo.TradeOutcome.
                outcome="signal_exit",
            )
        except ValueError as e:
            _journal_reject(pm_id, symbol, f"close_trade refused: {e}")
            result.rejected.append(f"{symbol}: {e}")
            continue
        _journal_closed(
            pm_id,
            symbol,
            existing.side,
            existing.quantity,
            signal.price,
            existing.id,
            closed.pnl_inr,
            signal.rationale,
        )
        result.closed.append(existing.id)

    return result


__all__ = ["DEFAULT_BAR_WINDOW", "DEFAULT_POSITION_FRACTION", "CycleResult", "run_trading_cycle"]
