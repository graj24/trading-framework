"""Behavioural tests for the momentum_v1 seed strategy.

Plan/01-KEYSTONE.md §5 Step 3.3 verification:

* The module imports cleanly (guards against future regressions in
  NautilusTrader / config layout).
* On a synthetic uptrend the strategy opens at least one position.
* On a synthetic downtrend (long-only strategy) the strategy opens
  zero positions.
* On an uptrend that flips to a downtrend, the strategy opens at
  least one position and that position eventually closes.

Synthetic bars are built directly via :class:`BarDataWrangler`,
mirroring the pattern used by the trading smoke. Bar invariants
(``low <= open, close <= high``) are enforced — the wrangler rejects
violators.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.persistence.wranglers import BarDataWrangler

from agora.apps.propfirm.seed_strategies.momentum_v1 import (
    MomentumV1,
    MomentumV1Config,
)
from agora.apps.propfirm.trading.engine import NSE_PAPER, build_backtest_engine
from agora.apps.propfirm.trading.instruments import nse_equity

SYMBOL = "RELIANCE"
BAR_TYPE_STR = f"{SYMBOL}.{NSE_PAPER.value}-1-DAY-LAST-EXTERNAL"


# ----- Synthetic bar factories ----------------------------------------------


def _build_bars_from_closes(closes: list[float]) -> list[Bar]:
    """Wrangle a list of close prices into NautilusTrader Bars.

    Each bar's open is the previous close (or the first close for bar 0),
    high/low add a small spread, volume is a constant. Enforces the
    ``low <= open, close <= high`` invariant required by
    :class:`BarDataWrangler`.
    """
    instrument = nse_equity(SYMBOL)
    bar_type = BarType.from_str(BAR_TYPE_STR)

    rng = random.Random(7)
    rows: list[dict[str, float]] = []
    timestamps: list[datetime] = []
    start = datetime(2025, 1, 1, tzinfo=UTC)
    prev_close = closes[0]
    for i, close in enumerate(closes):
        open_ = prev_close
        high = max(open_, close) + abs(rng.uniform(0.1, 1.0))
        low = min(open_, close) - abs(rng.uniform(0.1, 1.0))
        rows.append(
            {
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
                "volume": 50_000.0,
            }
        )
        timestamps.append(start + timedelta(days=i))
        prev_close = close

    df = pd.DataFrame(rows, index=pd.DatetimeIndex(timestamps, name="timestamp"))
    return list(BarDataWrangler(bar_type, instrument).process(df))


def _uptrend_closes(n: int = 120, start: float = 1000.0, drift: float = 5.0) -> list[float]:
    """Steady positive drift. Small random noise so bars are non-degenerate."""
    rng = random.Random(11)
    closes: list[float] = []
    price = start
    for _ in range(n):
        price += drift + rng.uniform(-1.0, 1.0)
        closes.append(price)
    return closes


def _downtrend_closes(n: int = 120, start: float = 2000.0, drift: float = 5.0) -> list[float]:
    """Steady negative drift, mirror of the uptrend."""
    rng = random.Random(13)
    closes: list[float] = []
    price = start
    for _ in range(n):
        price -= drift + rng.uniform(-1.0, 1.0)
        closes.append(max(1.0, price))
    return closes


# ----- Test cases -----------------------------------------------------------


def test_strategy_imports_without_sandbox_error() -> None:
    """Sentinel: a fresh import of momentum_v1 should not raise.

    Guards against future code that breaks the import (a missing
    NautilusTrader symbol, a config layout change, etc.).
    """
    import agora.apps.propfirm.seed_strategies.momentum_v1 as m

    assert m.MomentumV1 is not None
    assert m.MomentumV1Config is not None


def _run_backtest(closes: list[float]) -> pd.DataFrame:
    """Build an engine, feed ``closes``, return the positions report."""
    instrument = nse_equity(SYMBOL)
    bar_type = BarType.from_str(BAR_TYPE_STR)
    bars = _build_bars_from_closes(closes)

    engine = build_backtest_engine()
    try:
        engine.add_instrument(instrument)
        engine.add_data(bars)
        engine.add_strategy(
            MomentumV1(
                MomentumV1Config(
                    instrument_id=instrument.id,
                    bar_type=bar_type,
                )
            )
        )
        engine.run()
        return engine.trader.generate_positions_report()
    finally:
        engine.dispose()


def test_strategy_in_backtest_with_uptrending_data() -> None:
    """A clear, sustained uptrend should generate at least one long entry."""
    closes = _uptrend_closes()
    positions = _run_backtest(closes)
    assert not positions.empty, "expected at least one position on an uptrend"


def test_strategy_in_backtest_with_downtrending_data() -> None:
    """Long-only strategy on a downtrend should never open a position."""
    closes = _downtrend_closes()
    positions = _run_backtest(closes)
    assert positions.empty, f"long-only strategy opened {len(positions)} positions on a downtrend"


def test_strategy_exits_on_signal_reversal() -> None:
    """Uptrend then downtrend: at least one position opens and closes.

    The exact bar where the close fires is sensitive to indicator
    warm-up math — we just assert that *some* position eventually
    closes, which is what matters for the plumbing.
    """
    closes = _uptrend_closes(n=120) + _downtrend_closes(n=120, start=1600.0)
    positions = _run_backtest(closes)
    assert not positions.empty, "expected at least one position over up-then-down regime"
    # ``ts_closed`` is non-null when the position was closed during the run.
    closed = positions[positions["ts_closed"].notna()]
    assert not closed.empty, "expected at least one position to close after regime flip"


@pytest.mark.parametrize(
    "closes_factory",
    [_uptrend_closes, _downtrend_closes],
)
def test_strategy_backtest_completes_cleanly(
    closes_factory: object,
) -> None:
    """Engine.run() must complete without raising for both regimes."""
    closes = closes_factory()  # type: ignore[operator]
    # If this raises, the fixture (engine + strategy + data) is the bug,
    # not the assertion above.
    _run_backtest(closes)
