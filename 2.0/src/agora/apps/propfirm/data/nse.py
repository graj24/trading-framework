"""NSE market data adapter.

K3 Step 3.2. Reads daily OHLCV bars from local parquet files and serves
them as either lightweight :class:`Quote` snapshots or NautilusTrader
:class:`Bar` objects. The interface is deliberately pluggable —
:class:`MarketDataAdapter` defines the contract, K3+ swaps the file
backend for a real provider (Groww, NSE direct, paid feed).

Contract:

* :meth:`MarketDataAdapter.snapshot` ``(symbols)`` →
  ``dict[symbol, latest Quote]``.
* :meth:`MarketDataAdapter.bars` ``(symbol, n)`` → ``list[Bar]`` with
  the most recent ``n`` bars in chronological order (oldest first,
  newest last).

For dev: :class:`ParquetMarketData` reads the legacy
``<repo-root>/stocks/<SYMBOL>/price_history.parquet`` files (one level
up from the AGORA 2.0 tree). For prod: a future ``MarketDataAdapter``
subclass swaps the file backend.

Resolution path for the stocks root, in order:

1. Explicit ``stocks_root`` constructor argument.
2. ``AGORA_STOCKS_ROOT`` environment variable.
3. ``<repo-2.0-parent>/stocks/`` resolved from this module's ``__file__``.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pandas as pd
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.instruments import Equity
from nautilus_trader.persistence.wranglers import BarDataWrangler

from agora.apps.propfirm.trading.engine import NSE_PAPER
from agora.apps.propfirm.trading.instruments import nse_equity

#: Standard column rename: legacy yfinance-shaped parquet uses Title
#: case; NautilusTrader's wrangler wants lowercase.
_RENAME = {
    "Open": "open",
    "High": "high",
    "Low": "low",
    "Close": "close",
    "Volume": "volume",
}

InstrumentResolver = Callable[[str], Equity]


@dataclass(frozen=True)
class Quote:
    """Latest-tick quote for a single symbol.

    Lightweight on purpose — the K3 PMs only need a price + timestamp.
    Full ``QuoteTick`` plumbing arrives when we wire a live feed.
    """

    symbol: str
    price: float
    ts: datetime


def resolve_default_stocks_root() -> Path:
    """Return the default location of the legacy ``stocks/`` tree.

    Order: ``AGORA_STOCKS_ROOT`` env var, then the conventional layout
    ``<repo-2.0-parent>/stocks/`` derived from this file's path. The
    returned path is *not* required to exist; the caller decides what
    to do with a missing root.
    """
    env = os.getenv("AGORA_STOCKS_ROOT")
    if env:
        return Path(env).resolve()
    # this file:   <repo>/2.0/src/agora/apps/propfirm/data/nse.py
    # parents[5]:  <repo>/2.0
    # parents[6]:  <repo>
    return (Path(__file__).resolve().parents[6] / "stocks").resolve()


class MarketDataAdapter:
    """Pluggable market-data interface.

    Subclasses must implement :meth:`snapshot` and :meth:`bars`. The
    methods are async so live feeds (which will hit the network) share
    one shape with the cached file backend.
    """

    async def snapshot(self, symbols: list[str]) -> dict[str, Quote]:
        raise NotImplementedError

    async def bars(self, symbol: str, n: int) -> list[Bar]:
        raise NotImplementedError


class ParquetMarketData(MarketDataAdapter):
    """Reads cached daily bars from
    ``<stocks_root>/<symbol>/price_history.parquet``.

    Parameters
    ----------
    stocks_root:
        Root directory holding ``<symbol>/price_history.parquet``.
        Defaults to :func:`resolve_default_stocks_root`.
    instrument_resolver:
        Callable that maps a symbol string to a NautilusTrader
        :class:`Equity`. The resolved instrument is needed by
        :class:`BarDataWrangler` for price/size precision. Defaults to
        :func:`agora.apps.propfirm.trading.instruments.nse_equity`.

    Caching is in-process and lazy: each parquet file is read at most
    once per adapter instance. The cache lives on the instance, not at
    module scope, so test runs do not bleed state between cases.
    """

    def __init__(
        self,
        stocks_root: Path | str | None = None,
        *,
        instrument_resolver: InstrumentResolver = nse_equity,
    ) -> None:
        self._root = (
            Path(stocks_root).resolve()
            if stocks_root is not None
            else resolve_default_stocks_root()
        )
        self._instrument_resolver = instrument_resolver
        self._frame_cache: dict[str, pd.DataFrame] = {}
        self._read_count: dict[str, int] = {}

    # ----- Public API -------------------------------------------------------

    async def snapshot(self, symbols: list[str]) -> dict[str, Quote]:
        out: dict[str, Quote] = {}
        for symbol in symbols:
            df = self._load(symbol)
            last = df.iloc[-1]
            ts = df.index[-1]
            # ``ts`` may be a pandas Timestamp; normalise to stdlib
            # datetime so downstream consumers don't carry a pandas dep.
            ts_py = ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts
            out[symbol] = Quote(
                symbol=symbol,
                price=float(last["Close"]),
                ts=ts_py,
            )
        return out

    async def bars(self, symbol: str, n: int) -> list[Bar]:
        if n <= 0:
            raise ValueError(f"n must be positive, got {n}")
        df = self._load(symbol).tail(n)
        instrument = self._instrument_resolver(symbol)
        bar_type = BarType.from_str(f"{symbol}.{NSE_PAPER.value}-1-DAY-LAST-EXTERNAL")
        normalised = _normalise_ohlcv(df)
        bars: list[Bar] = list(BarDataWrangler(bar_type, instrument).process(normalised))
        return bars

    # ----- Internals --------------------------------------------------------

    def _parquet_path(self, symbol: str) -> Path:
        return self._root / symbol / "price_history.parquet"

    def _load(self, symbol: str) -> pd.DataFrame:
        if symbol in self._frame_cache:
            return self._frame_cache[symbol]
        path = self._parquet_path(symbol)
        if not path.is_file():
            raise FileNotFoundError(
                f"no parquet history for symbol {symbol!r} at {path}; "
                f"check stocks_root={self._root!s} or set AGORA_STOCKS_ROOT"
            )
        df = pd.read_parquet(path)
        self._frame_cache[symbol] = df
        self._read_count[symbol] = self._read_count.get(symbol, 0) + 1
        return df


def _normalise_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    """Rename Title-case OHLCV columns to lowercase for the wrangler.

    Tolerates frames already in lowercase form (test fixtures may write
    either). Drops auxiliary columns the wrangler doesn't expect.
    """
    rename = {k: v for k, v in _RENAME.items() if k in df.columns}
    df = df.rename(columns=rename)
    keep = [c for c in ("open", "high", "low", "close", "volume") if c in df.columns]
    return df[keep]


# ----- CLI ------------------------------------------------------------------


async def _amain(symbol: str, n: int) -> int:
    adapter = ParquetMarketData()
    try:
        bars = await adapter.bars(symbol, n)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    for bar in bars:
        print(bar)
    return 0


def main() -> int:
    import asyncio

    symbol = sys.argv[1] if len(sys.argv) > 1 else "RELIANCE"
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 5
    return asyncio.run(_amain(symbol, n))


if __name__ == "__main__":
    raise SystemExit(main())
