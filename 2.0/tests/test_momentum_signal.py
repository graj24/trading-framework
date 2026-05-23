"""Tests for the pure-Python momentum signal computer.

K3 Step 3.5. The signal is what the trading-cycle activity uses
instead of booting a NautilusTrader strategy per cycle. Behaviour
must mirror :mod:`agora.apps.propfirm.seed_strategies.momentum_v1`:

* SMA(20)/SMA(50) crossover gate for long entries.
* Signal-reversal exits when in a position.
* ATR(14) stop attached on entry.
* Insufficient data is a NONE, not an exception.
"""

from __future__ import annotations

from decimal import Decimal

from agora.apps.propfirm.seed_strategies.signals import compute_momentum_signal


def _series_from_closes(closes: list[float]) -> tuple[list[float], list[float], list[float]]:
    """Build matching highs / lows around the close stream.

    Highs/lows widen by ±0.5 around each close to keep the bars
    non-degenerate. The signal logic uses highs/lows only inside the
    ATR computation; precise values don't matter as long as the
    invariants hold.
    """
    highs = [c + 0.5 for c in closes]
    lows = [c - 0.5 for c in closes]
    return closes, highs, lows


def test_long_signal_when_uptrend_and_no_position() -> None:
    """60 monotonically rising closes → LONG when flat."""
    closes = [100.0 + i * 0.5 for i in range(60)]
    c, h, low = _series_from_closes(closes)

    sig = compute_momentum_signal(
        "RELIANCE",
        c,
        h,
        low,
        is_in_position=False,
    )

    assert sig.kind == "LONG"
    assert sig.symbol == "RELIANCE"
    assert sig.price == Decimal(str(closes[-1]))
    assert sig.stop_loss is not None
    # ATR-based stop must sit below entry; the rationale must mention
    # both SMAs and the close-vs-fast-SMA condition.
    assert sig.stop_loss < sig.price
    assert "sma20" in sig.rationale
    assert "sma50" in sig.rationale


def test_no_signal_when_downtrend_and_no_position() -> None:
    """60 monotonically falling closes → NONE; long-only strategy."""
    closes = [200.0 - i * 0.5 for i in range(60)]
    c, h, low = _series_from_closes(closes)

    sig = compute_momentum_signal("TCS", c, h, low, is_in_position=False)

    assert sig.kind == "NONE"
    assert sig.stop_loss is None
    # Rationale should describe the flat (no-position) branch.
    assert "flat" in sig.rationale.lower() or "no entry" in sig.rationale.lower()


def test_exit_signal_when_in_position_and_sma_crossdown() -> None:
    """Uptrend then downtrend, in_position=True → EXIT.

    Long enough downtrend after the uptrend that the SMA(20) drops
    below the SMA(50): the signal must flip to EXIT.
    """
    uptrend = [100.0 + i * 1.0 for i in range(60)]
    downtrend = [uptrend[-1] - i * 2.5 for i in range(80)]
    closes = uptrend + downtrend
    c, h, low = _series_from_closes(closes)

    sig = compute_momentum_signal("INFY", c, h, low, is_in_position=True)

    assert sig.kind == "EXIT"
    assert sig.stop_loss is None
    # Rationale mentions the SMA reversal explicitly so the journal
    # tells operators why we exited.
    assert "reversal" in sig.rationale or "<=" in sig.rationale


def test_no_signal_when_in_position_and_uptrend_continues() -> None:
    """Uptrend, in_position=True → NONE (hold)."""
    closes = [100.0 + i * 0.5 for i in range(60)]
    c, h, low = _series_from_closes(closes)

    sig = compute_momentum_signal("HDFCBANK", c, h, low, is_in_position=True)

    assert sig.kind == "NONE"
    assert sig.stop_loss is None
    assert "hold" in sig.rationale or "in position" in sig.rationale


def test_insufficient_bars_returns_none() -> None:
    """Fewer bars than max(slow_period, atr_period+1) → NONE with a
    rationale that says how many bars were short.
    """
    closes = [100.0 + i for i in range(30)]
    c, h, low = _series_from_closes(closes)

    sig = compute_momentum_signal("SBIN", c, h, low, is_in_position=False)

    assert sig.kind == "NONE"
    assert sig.stop_loss is None
    assert "insufficient" in sig.rationale
    assert "30" in sig.rationale  # have N
    assert "50" in sig.rationale  # need M
