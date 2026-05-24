"""SMA(20/50) momentum strategy with an ATR(14) stop.

K3 Step 3.3 — the seed strategy that proves the trading plumbing
works before LLM-driven evolution arrives in K4 (Reflection).

Signal logic per plan/01-KEYSTONE.md §5 Step 3.3:

* On every bar (daily): compute SMA(``fast_sma``=20), SMA(``slow_sma``=50),
  ATR(``atr_period``=14).
* **Long entry** when flat: ``fast_sma > slow_sma`` AND ``close > fast_sma``.
* **Exit** when in a long: ``fast_sma <= slow_sma`` (signal reversal)
  OR ``bar.low <= stop_price`` (ATR stop hit).
* **Long-only.** No shorts. NSE cash equity behaviour — no leverage in
  the default account configuration (see ``trading.engine``).
* **Position size**: ``floor(capital * pct_per_position / close)``,
  rounded down to the lot size of 1 share. If the resulting quantity
  is < 1, the signal is skipped (insufficient capital).

Drift from the plan: the ``max_positions`` constraint is *deferred*.
The K3 strategy is registered per-instrument, so each instance is
inherently single-position. Multi-instrument coordination (e.g. "don't
open a 6th position when 5 are already open across instruments") needs
a portfolio-level allocator, which is outside K3's scope. K4
(Reflection) touches strategy evolution; portfolio-level limits land
later.

NautilusTrader notes:

* Indicators come from the top-level ``nautilus_trader.indicators``
  re-export — that surface is stable across recent releases. The
  submodule paths (``indicators.averages``, ``indicators.volatility``)
  are also valid but the re-export keeps imports terser.
* :meth:`Strategy.register_indicator_for_bars` wires an indicator to a
  bar stream so NautilusTrader auto-feeds it on every bar — we don't
  call ``indicator.update_raw()`` ourselves.
* Capital lookup: ``self.portfolio.account(venue).balance_total(INR)``
  returns a :class:`Money`. We coerce to :class:`Decimal` for sizing
  math so we never multiply :class:`Money` directly.
* :meth:`Strategy.close_position` builds a reduce-only market order from
  the open :class:`Position`. We pull the position from
  ``self.cache.positions_open(instrument_id=...)``.
"""

from __future__ import annotations

from decimal import ROUND_DOWN, Decimal

from nautilus_trader.indicators import AverageTrueRange, SimpleMovingAverage
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import Quantity
from nautilus_trader.trading.config import StrategyConfig
from nautilus_trader.trading.strategy import Strategy

from agora.apps.propfirm.trading.engine import NSE_PAPER

__all__ = ["MomentumV1", "MomentumV1Config"]


class MomentumV1Config(StrategyConfig, frozen=True):
    """Frozen config for :class:`MomentumV1`.

    NautilusTrader strategies require a :class:`StrategyConfig` subclass.
    All sizing/timing parameters live here so the strategy class is pure
    behaviour.
    """

    instrument_id: InstrumentId
    bar_type: BarType
    fast_sma: int = 20
    slow_sma: int = 50
    atr_period: int = 14
    atr_stop_mult: float = 2.0
    pct_per_position: float = 0.05  # 5%
    max_positions: int = 5  # deferred (see module docstring)


class MomentumV1(Strategy):  # type: ignore[misc]  # nautilus_trader has no stubs
    """Long-only SMA crossover with ATR stop loss.

    See module docstring for the signal logic and the deferred
    ``max_positions`` note. One instance == one instrument == one
    position at a time.
    """

    config: MomentumV1Config

    def __init__(self, config: MomentumV1Config) -> None:
        super().__init__(config)
        self.fast_sma = SimpleMovingAverage(config.fast_sma)
        self.slow_sma = SimpleMovingAverage(config.slow_sma)
        self.atr = AverageTrueRange(config.atr_period)
        # Updated on entry, cleared on exit.
        self._stop_price: Decimal | None = None

    # ----- Lifecycle --------------------------------------------------------

    def on_start(self) -> None:
        self.register_indicator_for_bars(self.config.bar_type, self.fast_sma)
        self.register_indicator_for_bars(self.config.bar_type, self.slow_sma)
        self.register_indicator_for_bars(self.config.bar_type, self.atr)
        self.subscribe_bars(self.config.bar_type)

    def on_stop(self) -> None:
        self.unsubscribe_bars(self.config.bar_type)

    # ----- Bar handling -----------------------------------------------------

    def on_bar(self, bar: Bar) -> None:
        # Wait for all three indicators to warm up. ``slow_sma`` is the
        # slowest of the three at default settings.
        if not self.indicators_initialized():
            return

        fast = self.fast_sma.value
        slow = self.slow_sma.value
        close = bar.close.as_double()

        is_flat = self.portfolio.is_flat(self.config.instrument_id)

        if is_flat:
            if fast > slow and close > fast:
                self._enter_long(bar)
            return

        # In a long position — evaluate exits.
        self._maybe_exit(bar, fast=fast, slow=slow)

    # ----- Entry / exit -----------------------------------------------------

    def _enter_long(self, bar: Bar) -> None:
        qty_int = self._size_for(bar)
        if qty_int < 1:
            self.log.info(
                f"momentum_v1: skipping entry on "
                f"{self.config.instrument_id} — insufficient capital "
                f"for >= 1 share at close={bar.close}"
            )
            return

        # ATR-based stop: 2 * ATR(14) below entry close.
        entry_price = Decimal(str(bar.close))
        atr_value = Decimal(str(self.atr.value))
        stop = entry_price - atr_value * Decimal(str(self.config.atr_stop_mult))
        self._stop_price = stop

        order = self.order_factory.market(
            instrument_id=self.config.instrument_id,
            order_side=OrderSide.BUY,
            quantity=Quantity.from_int(qty_int),
        )
        self.submit_order(order)
        self.log.info(
            f"momentum_v1: BUY {qty_int} {self.config.instrument_id} "
            f"@ ~{entry_price} stop={stop:.2f}"
        )

    def _maybe_exit(self, bar: Bar, *, fast: float, slow: float) -> None:
        # 1. Signal reversal — fast SMA crossed back below slow SMA.
        if fast <= slow:
            self._close_open_position(reason="sma_crossdown")
            return

        # 2. Stop hit — bar's low pierced the ATR stop.
        if self._stop_price is not None and Decimal(str(bar.low)) <= self._stop_price:
            self._close_open_position(reason="stop_hit")
            return

    def _close_open_position(self, *, reason: str) -> None:
        positions = self.cache.positions_open(
            venue=NSE_PAPER, instrument_id=self.config.instrument_id
        )
        for position in positions:
            self.close_position(position)
            self.log.info(
                f"momentum_v1: CLOSE {position.quantity} "
                f"{self.config.instrument_id} reason={reason}"
            )
        self._stop_price = None

    # ----- Sizing -----------------------------------------------------------

    def _size_for(self, bar: Bar) -> int:
        """Compute the order quantity for a fresh long entry.

        Returns the number of shares to buy, lot-aligned to 1. Returns 0
        when the account balance is missing or below one share's price.
        """
        account = self.portfolio.account(NSE_PAPER)
        if account is None:
            return 0
        # Margin accounts on the NSEPAPER venue are INR-denominated; the
        # base_currency is set in build_backtest_engine. Asking for the
        # default currency keeps this strategy generic.
        balance = account.balance_total()
        if balance is None:
            return 0
        capital = Decimal(str(balance.as_decimal()))
        budget = (capital * Decimal(str(self.config.pct_per_position))).quantize(
            Decimal("0.01"), rounding=ROUND_DOWN
        )
        close = Decimal(str(bar.close))
        if close <= 0:
            return 0
        qty = (budget / close).to_integral_value(rounding=ROUND_DOWN)
        return int(qty)
