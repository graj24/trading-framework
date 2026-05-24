"""Market data layer for the prop-firm app.

K3 Step 3.2. The :class:`MarketDataAdapter` interface plus a
parquet-backed implementation for dev/test.
"""

from __future__ import annotations

from agora.apps.propfirm.data.nse import (
    MarketDataAdapter,
    ParquetMarketData,
    Quote,
    resolve_default_stocks_root,
)

__all__ = [
    "MarketDataAdapter",
    "ParquetMarketData",
    "Quote",
    "resolve_default_stocks_root",
]
