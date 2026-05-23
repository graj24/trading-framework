"""Tests for the K3 Step 3.1 engine + instruments surface.

Covers:

* ``build_backtest_engine`` returns a runnable engine with the
  ``NSEPAPER`` venue registered.
* ``nse_equity`` mints an INR-denominated, NSEPAPER-tagged Equity.
* ``NIFTY_50_SYMBOLS`` overlaps with the legacy parquet directory
  (guard rail against silent symbol drift). Self-skips when the legacy
  ``stocks/`` tree is not present (e.g. CI runners without the
  side-by-side checkout).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.model.currencies import INR
from nautilus_trader.model.identifiers import Venue

from agora.apps.propfirm.trading import (
    NIFTY_50_SYMBOLS,
    NSE_PAPER,
    build_backtest_engine,
    nse_equity,
)

LEGACY_STOCKS_ROOT = Path(__file__).resolve().parents[2] / "stocks"


def test_engine_factory_returns_runnable_engine() -> None:
    engine = build_backtest_engine()
    try:
        assert isinstance(engine, BacktestEngine)
        assert Venue("NSEPAPER") == NSE_PAPER
        assert NSE_PAPER in engine.list_venues()
    finally:
        engine.dispose()


def test_engine_factory_accepts_custom_starting_capital() -> None:
    engine = build_backtest_engine(starting_capital_inr=2_500_000)
    try:
        # Smoke: factory accepted the override and returned an engine.
        # We do not pry into the venue's account state; that is a
        # NautilusTrader internal and tested upstream.
        assert NSE_PAPER in engine.list_venues()
    finally:
        engine.dispose()


def test_nse_equity_constructs_with_inr_currency() -> None:
    equity = nse_equity("RELIANCE")

    assert str(equity.id.symbol) == "RELIANCE"
    assert equity.id.venue == NSE_PAPER
    # Equity stores the listing currency on quote_currency (NautilusTrader
    # normalizes the constructor arg ``currency`` -> ``quote_currency``).
    assert equity.quote_currency == INR
    assert str(equity.price_increment) == "0.05"
    assert int(equity.lot_size) == 1


def test_nifty_50_symbols_match_local_parquet_files() -> None:
    if not LEGACY_STOCKS_ROOT.is_dir():
        pytest.skip(
            f"legacy parquet root not present at {LEGACY_STOCKS_ROOT}; "
            "this guard rail is only meaningful with the side-by-side "
            "stocks/ checkout"
        )

    on_disk = {
        p.parent.name for p in LEGACY_STOCKS_ROOT.glob("*/price_history.parquet") if p.is_file()
    }
    assert on_disk, "legacy stocks/ tree exists but no parquet files found"

    declared = set(NIFTY_50_SYMBOLS)
    overlap = declared & on_disk
    assert overlap, (
        f"NIFTY_50_SYMBOLS={sorted(declared)} has no overlap with "
        f"on-disk parquet symbols={sorted(on_disk)}"
    )
    # Stronger: every declared symbol should have data on disk. If not,
    # the constant has drifted from reality and tests downstream will
    # break in the wrong place.
    missing = declared - on_disk
    assert not missing, f"declared symbols missing parquet history: {sorted(missing)}"
