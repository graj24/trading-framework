"""Tests for the K3 Step 3.2 NSE market data adapter.

Covers:

* ``snapshot()`` returns the latest Quote per symbol from synthetic
  parquet fixtures.
* ``bars()`` returns the last N bars in chronological order.
* Missing-symbol handling raises a clear ``FileNotFoundError`` (not a
  silent empty list).
* Caching: the parquet file is read at most once per symbol per
  adapter instance.
* ``bars()`` returns NautilusTrader :class:`Bar` objects.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from nautilus_trader.model.data import Bar

from agora.apps.propfirm.data.nse import ParquetMarketData


def _write_synthetic_parquet(path: Path, days: int = 100) -> None:
    """Write a deterministic synthetic OHLCV parquet at ``path``.

    Schema mirrors the legacy ``stocks/<SYM>/price_history.parquet``:
    Title-cased OHLCV, ``Dividends``, ``Stock Splits`` columns plus a
    ``Date`` index in IST. Bar invariants (``low <= open, close <= high``)
    are enforced so :class:`BarDataWrangler` accepts the data.
    """
    rng = np.random.default_rng(42)
    n = days
    base = 100 + np.cumsum(rng.normal(0, 1, n))
    open_ = base
    close = base + rng.normal(0, 0.1, n)
    spread = np.abs(rng.normal(0, 0.5, n))
    high = np.maximum(open_, close) + spread
    low = np.minimum(open_, close) - spread
    df = pd.DataFrame(
        {
            "Open": open_,
            "High": high,
            "Low": low,
            "Close": close,
            "Volume": rng.integers(1_000, 100_000, n).astype(float),
            "Dividends": np.zeros(n),
            "Stock Splits": np.zeros(n),
        },
        index=pd.date_range("2025-01-01", periods=n, freq="B", tz="Asia/Kolkata", name="Date"),
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path)


@pytest.fixture
def stocks_root(tmp_path: Path) -> Path:
    root = tmp_path / "stocks"
    _write_synthetic_parquet(root / "RELIANCE" / "price_history.parquet")
    _write_synthetic_parquet(root / "TCS" / "price_history.parquet", days=50)
    return root


async def test_snapshot_returns_latest_quote_per_symbol(stocks_root: Path) -> None:
    adapter = ParquetMarketData(stocks_root)
    quotes = await adapter.snapshot(["RELIANCE", "TCS"])

    assert set(quotes) == {"RELIANCE", "TCS"}
    for symbol, quote in quotes.items():
        assert quote.symbol == symbol
        assert quote.price > 0
        assert quote.ts is not None


async def test_bars_returns_last_n_in_order(stocks_root: Path) -> None:
    adapter = ParquetMarketData(stocks_root)
    bars = await adapter.bars("RELIANCE", n=10)

    assert len(bars) == 10
    timestamps = [b.ts_event for b in bars]
    assert timestamps == sorted(timestamps), "bars must be in chronological order"


async def test_missing_symbol_raises_clear_error(stocks_root: Path) -> None:
    adapter = ParquetMarketData(stocks_root)
    with pytest.raises(FileNotFoundError) as excinfo:
        await adapter.bars("DOESNOTEXIST", n=5)
    msg = str(excinfo.value)
    assert "DOESNOTEXIST" in msg
    assert "AGORA_STOCKS_ROOT" in msg


async def test_caching_avoids_re_read(stocks_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import pandas as pd_module

    adapter = ParquetMarketData(stocks_root)
    call_count = {"n": 0}
    original = pd_module.read_parquet

    def counting(*args: object, **kwargs: object) -> object:
        call_count["n"] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(pd_module, "read_parquet", counting)

    await adapter.bars("RELIANCE", n=5)
    await adapter.bars("RELIANCE", n=10)
    await adapter.snapshot(["RELIANCE"])

    assert call_count["n"] == 1, f"expected single parquet read for RELIANCE, got {call_count['n']}"


async def test_bars_returns_nautilus_trader_bar_objects(stocks_root: Path) -> None:
    adapter = ParquetMarketData(stocks_root)
    bars = await adapter.bars("TCS", n=3)

    assert bars
    for bar in bars:
        assert isinstance(bar, Bar)


async def test_n_must_be_positive(stocks_root: Path) -> None:
    adapter = ParquetMarketData(stocks_root)
    with pytest.raises(ValueError):
        await adapter.bars("RELIANCE", n=0)
