"""NautilusTrader integration for the AGORA prop-firm app.

K3 Step 3.1 wires a NautilusTrader ``BacktestEngine`` against a simulated
``NSE-PAPER`` venue with INR margin accounting. Public surface:

* :data:`NSE_PAPER`            — the canonical venue identifier.
* :func:`build_backtest_engine` — engine factory.
* :func:`nse_equity`           — Equity instrument helper.
* :data:`NIFTY_50_SYMBOLS`     — symbols with local parquet history.
"""

from __future__ import annotations

from agora.apps.propfirm.trading.engine import NSE_PAPER, build_backtest_engine
from agora.apps.propfirm.trading.instruments import NIFTY_50_SYMBOLS, nse_equity

__all__ = [
    "NIFTY_50_SYMBOLS",
    "NSE_PAPER",
    "build_backtest_engine",
    "nse_equity",
]
