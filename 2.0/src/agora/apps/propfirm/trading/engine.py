"""NautilusTrader engine factory for the simulated ``NSEPAPER`` venue.

K3 Step 3.1. The factory builds a ``BacktestEngine`` configured for Indian
equity paper trading:

* Venue ``NSEPAPER`` (the simulated NSE we paper-trade against). The name
  is deliberately hyphen-free: NautilusTrader's ``BacktestExecClient``
  derives the account issuer by splitting the venue on ``-`` and asserts
  the issuer matches the venue id; ``"NSE-PAPER"`` blows that assertion
  with ``"id.value of NSE-PAPER was not equal to account_id.get_issuer()
  of NSE"``. ``NSEPAPER`` keeps the round-trip consistent.
* ``OmsType.NETTING`` — long/short positions net per instrument, matching
  how real cash equity accounts behave at NSE.
* ``AccountType.MARGIN`` — required for ``default_leverage`` to take effect
  (the cash account type ignores it). Default leverage is ``1`` so the
  account behaves like cash unless caller overrides.
* ``base_currency=INR`` — INR ships in ``nautilus_trader.model.currencies``
  out of the box. We confirmed at K3 Step 3.0 recon that no custom
  ``Currency.from_internal_map`` registration is needed.

Notes for downstream steps:

* The strategy/data wiring lives in 3.2 (market data) and 3.3 (seed
  strategy). This module is deliberately the "thin shell" that gets the
  engine onto its feet.
* The engine is *not* a global singleton. Every call returns a fresh
  engine; callers are responsible for ``dispose()``-ing when done.
"""

from __future__ import annotations

from decimal import Decimal

from nautilus_trader.backtest.engine import BacktestEngine, BacktestEngineConfig
from nautilus_trader.common.config import LoggingConfig
from nautilus_trader.model.currencies import INR
from nautilus_trader.model.enums import AccountType, OmsType
from nautilus_trader.model.identifiers import Venue
from nautilus_trader.model.objects import Money

#: Canonical paper-trading venue. Used everywhere we tag instruments,
#: bars, or orders for the simulated NSE. No hyphen — see module
#: docstring for the NautilusTrader account-issuer constraint.
NSE_PAPER: Venue = Venue("NSEPAPER")

#: Default starting capital in INR for a fresh PM. Conservative for a
#: prop-firm seed; PMs will scale up as they earn the right to.
DEFAULT_STARTING_CAPITAL_INR: int = 1_000_000


def build_backtest_engine(
    *,
    starting_capital_inr: int | Decimal = DEFAULT_STARTING_CAPITAL_INR,
    default_leverage: Decimal | float | int = Decimal(1),
    bypass_logging: bool = True,
    log_level: str = "INFO",
) -> BacktestEngine:
    """Construct a ``BacktestEngine`` with the NSE-PAPER venue registered.

    Parameters
    ----------
    starting_capital_inr:
        Account starting balance, in INR. Wrapped into a ``Money`` and
        passed as ``starting_balances=[...]``.
    default_leverage:
        Default leverage on the margin account. ``1`` means cash-like.
    bypass_logging:
        If ``True`` (default), NautilusTrader's logging is suppressed so
        the engine does not flood test output. Tests and the smoke flip
        this to keep the output focused. Production setups will want
        ``False`` plus ``log_level="INFO"``.
    log_level:
        Forwarded to ``LoggingConfig.log_level``. Only meaningful when
        ``bypass_logging`` is ``False``.

    Returns
    -------
    BacktestEngine
        Engine with one venue (``NSE-PAPER``). The caller adds
        instruments + data + strategy, then calls ``run()``.
    """
    config = BacktestEngineConfig(
        trader_id="AGORA-PROPFIRM-001",
        logging=LoggingConfig(log_level=log_level, bypass_logging=bypass_logging),
    )
    engine = BacktestEngine(config=config)
    engine.add_venue(
        venue=NSE_PAPER,
        oms_type=OmsType.NETTING,
        account_type=AccountType.MARGIN,
        starting_balances=[Money(starting_capital_inr, INR)],
        base_currency=INR,
        default_leverage=Decimal(default_leverage),
    )
    return engine
