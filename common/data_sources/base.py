"""Pluggable DataSource interface.

Each PM can register its own data sources. The framework provides
GrowwSource and YFinanceSource as built-ins.

Usage:
    from common.data_sources import get_source, register_source
    quote = get_source("yfinance").get_quote("RELIANCE")
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)


class NotSupported(NotImplementedError):
    """Raised when a DataSource doesn't implement a method."""


@dataclass
class Quote:
    symbol: str
    ltp: float
    open: float = 0.0
    high: float = 0.0
    low: float = 0.0
    close: float = 0.0
    volume: int = 0
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class NewsItem:
    headline: str
    source: str = ""
    url: str = ""
    published_at: datetime = field(default_factory=datetime.now)
    sentiment: float = 0.0  # -1 to +1


class DataSource(ABC):
    """Abstract base for all data sources."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique identifier for this source."""

    def get_quote(self, symbol: str) -> Quote:
        raise NotSupported(f"{self.name} does not support get_quote")

    def get_history(self, symbol: str, period: str = "1y", interval: str = "1d") -> Any:
        """Return a pandas DataFrame or raise NotSupported."""
        raise NotSupported(f"{self.name} does not support get_history")

    def get_news(self, symbol: str, limit: int = 10) -> list[NewsItem]:
        raise NotSupported(f"{self.name} does not support get_news")

    def search_symbols(self, query: str) -> list[str]:
        raise NotSupported(f"{self.name} does not support search_symbols")


# ── Built-in: YFinance ────────────────────────────────────────────────────────

class YFinanceSource(DataSource):
    @property
    def name(self) -> str:
        return "yfinance"

    def get_quote(self, symbol: str) -> Quote:
        import yfinance as yf
        from common.core.symbols import to_yfinance_ticker
        ticker = to_yfinance_ticker(symbol)
        t = yf.Ticker(ticker)
        hist = t.history(period="1d")
        if hist.empty:
            return Quote(symbol=symbol, ltp=0.0)
        row = hist.iloc[-1]
        return Quote(
            symbol=symbol,
            ltp=float(row["Close"]),
            open=float(row["Open"]),
            high=float(row["High"]),
            low=float(row["Low"]),
            close=float(row["Close"]),
            volume=int(row["Volume"]),
        )

    def get_history(self, symbol: str, period: str = "1y", interval: str = "1d") -> Any:
        import yfinance as yf
        from common.core.symbols import to_yfinance_ticker
        return yf.Ticker(to_yfinance_ticker(symbol)).history(period=period, interval=interval)


# ── Built-in: Groww ───────────────────────────────────────────────────────────

class GrowwSource(DataSource):
    @property
    def name(self) -> str:
        return "groww"

    def get_quote(self, symbol: str) -> Quote:
        try:
            from common.core.groww_client import GrowwClient
            client = GrowwClient()
            ltp = client.get_ltp(symbol)
            return Quote(symbol=symbol, ltp=ltp)
        except Exception as e:
            logger.debug(f"GrowwSource.get_quote({symbol}) failed: {e}")
            return Quote(symbol=symbol, ltp=0.0)


# ── Registry ──────────────────────────────────────────────────────────────────

_registry: dict[str, DataSource] = {}


def register_source(source: DataSource) -> None:
    """Register a DataSource instance by its name."""
    _registry[source.name] = source
    logger.debug(f"DataSource registered: {source.name}")


def get_source(name: str) -> DataSource:
    """Get a registered DataSource by name. Auto-registers built-ins."""
    if name not in _registry:
        _auto_register(name)
    if name not in _registry:
        raise KeyError(f"DataSource '{name}' not registered. Call register_source() first.")
    return _registry[name]


def list_sources() -> list[str]:
    return list(_registry.keys())


def _auto_register(name: str) -> None:
    """Lazily register built-in sources on first access."""
    if name == "yfinance":
        register_source(YFinanceSource())
    elif name == "groww":
        register_source(GrowwSource())
