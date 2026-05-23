"""Run ``momentum_v1`` against cached daily bars for one symbol.

Verification target for plan/01-KEYSTONE.md §5 Step 3.3.

Loads the last ``--bars`` (default 90) trading-day bars for ``--symbol``
(default ``RELIANCE``) from the ParquetMarketData adapter, runs the
NautilusTrader :class:`BacktestEngine` with :class:`MomentumV1`, and
prints the order fills and positions reports. Exits 0 on a clean run.

This is *not* a comprehensive backtest harness — it's a smoke that
exercises the full plumbing (instruments → data adapter → engine →
strategy → fills/positions reports). Real evaluation lives downstream.

Run::

    make momentum-backtest
    # or
    uv run python -m agora.apps.propfirm.seed_strategies.momentum_backtest
"""

from __future__ import annotations

import asyncio
import sys

from nautilus_trader.model.data import BarType

from agora.apps.propfirm.data.nse import ParquetMarketData
from agora.apps.propfirm.seed_strategies.momentum_v1 import (
    MomentumV1,
    MomentumV1Config,
)
from agora.apps.propfirm.trading.engine import NSE_PAPER, build_backtest_engine
from agora.apps.propfirm.trading.instruments import nse_equity


async def _amain(symbol: str, n_bars: int) -> int:
    instrument = nse_equity(symbol)
    bar_type = BarType.from_str(f"{symbol}.{NSE_PAPER.value}-1-DAY-LAST-EXTERNAL")

    adapter = ParquetMarketData()
    try:
        bars = await adapter.bars(symbol, n=n_bars)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if not bars:
        print(f"error: no bars returned for {symbol}", file=sys.stderr)
        return 2

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

        fills = engine.trader.generate_order_fills_report()
        positions = engine.trader.generate_positions_report()
        print(
            f"OK — {symbol}: {len(bars)} bars, " f"{len(fills)} fills, {len(positions)} positions"
        )
        if not fills.empty:
            print("\n--- Fills ---")
            print(fills.to_string())
        if not positions.empty:
            print("\n--- Positions ---")
            print(positions.to_string())
        return 0
    finally:
        engine.dispose()


def main() -> int:
    symbol = sys.argv[1] if len(sys.argv) > 1 else "RELIANCE"
    n_bars = int(sys.argv[2]) if len(sys.argv) > 2 else 90
    return asyncio.run(_amain(symbol, n_bars))


if __name__ == "__main__":
    raise SystemExit(main())
