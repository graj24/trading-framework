"""Pure-Python signal computation for the momentum_v1 strategy.

K3 Step 3.5 needs to evaluate the signal once per trading cycle
without booting a full NautilusTrader ``BacktestEngine``. The math is
simple: SMA(20), SMA(50), ATR(14), and a rule that combines them into
a LONG / EXIT / NONE signal. Same logic as
:mod:`agora.apps.propfirm.seed_strategies.momentum_v1`, just stripped
to the per-cycle decision and ported off NautilusTrader's indicator
classes.

This module is used by the trading-cycle activity in
:mod:`agora.platform.workers.pm_supervisor`. It does NOT import
NautilusTrader — only the standard library and :mod:`decimal`. Pricing
arithmetic uses :class:`Decimal` so the broker's stop-loss and
quantity computations never see float drift.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

#: Signal kinds emitted by :func:`compute_momentum_signal`. ``LONG``
#: only fires when not in position; ``EXIT`` only fires when in
#: position; ``NONE`` covers everything else (insufficient bars, no
#: trend, in position with the trend still up).
SignalKind = Literal["LONG", "EXIT", "NONE"]


@dataclass(frozen=True)
class MomentumSignal:
    """Per-cycle decision emitted by :func:`compute_momentum_signal`.

    ``stop_loss`` is set only on ``LONG``: the entry-time ATR-based
    floor. The strategy/broker do not enforce intra-bar SL — see
    :func:`compute_momentum_signal` docstring. ``rationale`` is the
    human-readable explanation that the trading-cycle activity
    journals alongside the placement / skip / exit line.
    """

    kind: SignalKind
    symbol: str
    price: Decimal
    stop_loss: Decimal | None
    rationale: str


def _sma(values: list[float], period: int) -> float:
    """Arithmetic mean of the trailing ``period`` values.

    The caller must ensure ``len(values) >= period`` — this helper
    does no bounds checking so the caller can decide whether
    insufficient data is a SKIP or a NONE.
    """
    return sum(values[-period:]) / period


def _atr(highs: list[float], lows: list[float], closes: list[float], period: int) -> float:
    """Wilder-style true range averaged with a simple SMA.

    The momentum_v1 strategy uses NautilusTrader's
    :class:`AverageTrueRange` which defaults to a Wilder smoothing.
    For per-cycle decisions a plain SMA over the last ``period`` true
    ranges is close enough — the SL is a 2x band, not a tight stop,
    and the indicator is for sizing/SL placement only, not the entry
    rule. Using SMA-of-TR keeps the math closed-form, with no warm-up
    state to thread through cycles.

    True range:
      ``tr[i] = max(high[i] - low[i], |high[i] - close[i-1]|,
                    |low[i]  - close[i-1]|)``

    Requires ``len(closes) >= period + 1`` for a single ``close[i-1]``
    reference per TR.
    """
    trs: list[float] = []
    # ``closes[-(period + 1)]`` is the prev_close for the first TR we
    # care about; iterate the last ``period`` bars.
    start = len(closes) - period
    for i in range(start, len(closes)):
        prev_close = closes[i - 1]
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - prev_close),
            abs(lows[i] - prev_close),
        )
        trs.append(tr)
    return sum(trs) / period


def compute_momentum_signal(
    symbol: str,
    closes: list[float],
    highs: list[float],
    lows: list[float],
    *,
    fast_period: int = 20,
    slow_period: int = 50,
    atr_period: int = 14,
    atr_stop_mult: float = 2.0,
    is_in_position: bool,
) -> MomentumSignal:
    """Decide LONG / EXIT / NONE based on the latest close.

    ``closes``, ``highs``, ``lows`` are chronologically ordered (oldest
    first, latest last). The function needs at least
    ``max(slow_period, atr_period + 1)`` bars to evaluate; less than
    that returns ``NONE`` with a rationale describing the shortfall.

    Logic (matches :mod:`agora.apps.propfirm.seed_strategies.momentum_v1`):

    * **Not in position**:
        * ``LONG`` when ``sma_fast > sma_slow`` AND ``close > sma_fast``.
        * Otherwise ``NONE``.
    * **In position**:
        * ``EXIT`` when ``sma_fast <= sma_slow`` (signal reversal).
        * Otherwise ``NONE``.

    The ATR-based SL is emitted on entry but **not enforced here**:
    the broker writes ``stop_loss`` to ``paper_trades.stop_loss`` and
    the K3.6 EOD closer is responsible for intra-bar SL hits. K3.5's
    cycle is daily-bar driven, so SL via this signal is naturally
    lazy — the EXIT branch above handles the trend-reversal case.
    """
    n = len(closes)
    if n != len(highs) or n != len(lows):
        raise ValueError(
            f"closes/highs/lows length mismatch: "
            f"{n}/{len(highs)}/{len(lows)} for symbol {symbol!r}"
        )

    needed = max(slow_period, atr_period + 1)
    if n < needed:
        return MomentumSignal(
            kind="NONE",
            symbol=symbol,
            price=Decimal(str(closes[-1])) if closes else Decimal(0),
            stop_loss=None,
            rationale=f"insufficient bars: have {n}, need {needed}",
        )

    sma_fast = _sma(closes, fast_period)
    sma_slow = _sma(closes, slow_period)
    close = closes[-1]
    price = Decimal(str(close))

    if is_in_position:
        if sma_fast <= sma_slow:
            return MomentumSignal(
                kind="EXIT",
                symbol=symbol,
                price=price,
                stop_loss=None,
                rationale=(
                    f"sma{fast_period}({sma_fast:.2f}) <= "
                    f"sma{slow_period}({sma_slow:.2f}); signal reversal"
                ),
            )
        return MomentumSignal(
            kind="NONE",
            symbol=symbol,
            price=price,
            stop_loss=None,
            rationale=(
                f"in position; sma{fast_period}({sma_fast:.2f}) > "
                f"sma{slow_period}({sma_slow:.2f}); hold"
            ),
        )

    # Flat — evaluate entry.
    if sma_fast > sma_slow and close > sma_fast:
        atr_value = _atr(highs, lows, closes, atr_period)
        stop = price - Decimal(str(atr_value)) * Decimal(str(atr_stop_mult))
        return MomentumSignal(
            kind="LONG",
            symbol=symbol,
            price=price,
            stop_loss=stop,
            rationale=(
                f"sma{fast_period}({sma_fast:.2f}) > "
                f"sma{slow_period}({sma_slow:.2f}); "
                f"close({close:.2f}) > sma{fast_period}"
            ),
        )
    return MomentumSignal(
        kind="NONE",
        symbol=symbol,
        price=price,
        stop_loss=None,
        rationale=(
            f"flat; sma{fast_period}({sma_fast:.2f}) "
            f"vs sma{slow_period}({sma_slow:.2f}); "
            f"close={close:.2f}; no entry"
        ),
    )


__all__ = ["MomentumSignal", "SignalKind", "compute_momentum_signal"]
