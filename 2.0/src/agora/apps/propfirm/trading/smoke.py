"""End-to-end smoke for the NSE-PAPER engine.

K3 Step 3.1 verification target. Builds the engine via the factory, adds
one instrument (RELIANCE), pumps 100 synthetic random-walk daily bars
through a no-op ``Strategy`` subclass that just counts ``on_bar`` calls,
and prints a summary.

Run with::

    make trading-smoke
    # or
    uv run python -m agora.apps.propfirm.trading.smoke

Exit code is ``0`` on success and non-zero on any failure (engine
construction, data wrangling, run, or zero bars processed).
"""

from __future__ import annotations

import random
import sys
from datetime import UTC, datetime, timedelta

import pandas as pd
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.persistence.wranglers import BarDataWrangler
from nautilus_trader.trading.strategy import Strategy

from agora.apps.propfirm.trading.engine import build_backtest_engine
from agora.apps.propfirm.trading.instruments import nse_equity

# ----- Synthetic data -------------------------------------------------------


def _build_synthetic_bars(bar_type: BarType, instrument: object, n: int = 100) -> list[Bar]:
    """Build ``n`` random-walk daily OHLCV bars and wrangle them into
    NautilusTrader ``Bar`` objects.

    The bars start at 2025-01-01 and step one calendar day at a time. The
    walk is seeded for reproducibility so the smoke is deterministic.
    """
    rng = random.Random(42)
    rows: list[dict[str, float]] = []
    timestamps: list[datetime] = []
    price = 1500.0
    start = datetime(2025, 1, 1, tzinfo=UTC)
    for i in range(n):
        step = rng.uniform(-10.0, 10.0)
        open_ = price
        close = max(1.0, price + step)
        high = max(open_, close) + abs(rng.uniform(0, 5))
        low = min(open_, close) - abs(rng.uniform(0, 5))
        rows.append(
            {
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
                "volume": float(rng.randint(1_000, 100_000)),
            }
        )
        timestamps.append(start + timedelta(days=i))
        price = close
    df = pd.DataFrame(rows, index=pd.DatetimeIndex(timestamps, name="timestamp"))
    bars: list[Bar] = list(BarDataWrangler(bar_type, instrument).process(df))
    return bars


# ----- No-op strategy -------------------------------------------------------


class _CountingStrategy(Strategy):  # type: ignore[misc]  # nautilus_trader has no stubs
    """Subscribes to one ``BarType`` and counts ``on_bar`` invocations.

    No orders are placed; this exists purely to prove the engine plumbs
    bars to a strategy. Step 3.3 ships the seed momentum strategy.
    """

    def __init__(self, bar_type: BarType) -> None:
        super().__init__()
        self._bar_type = bar_type
        self.bars_seen: int = 0

    def on_start(self) -> None:
        self.subscribe_bars(self._bar_type)

    def on_bar(self, bar: Bar) -> None:
        self.bars_seen += 1


# ----- Smoke entrypoint -----------------------------------------------------


def main() -> int:
    symbol = "RELIANCE"
    instrument = nse_equity(symbol)
    bar_type = BarType.from_str(f"{symbol}.NSEPAPER-1-DAY-LAST-EXTERNAL")

    engine = build_backtest_engine()
    try:
        engine.add_instrument(instrument)
        bars = _build_synthetic_bars(bar_type, instrument, n=100)
        engine.add_data(bars)

        strategy = _CountingStrategy(bar_type)
        engine.add_strategy(strategy)

        engine.run()

        if strategy.bars_seen == 0:
            print("FAIL — strategy never received any bars", file=sys.stderr)
            return 1
        print(f"OK — {strategy.bars_seen} bars processed")
        return 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
