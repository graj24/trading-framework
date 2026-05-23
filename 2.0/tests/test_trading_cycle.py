"""Unit tests for the K3 Step 3.5 trading-cycle orchestration.

The cycle orchestrates: PM lookup -> watchlist -> per-symbol bars ->
signal -> place / close / skip. Each test stubs the adapter, the
broker, and the trade repo so the cycle's branching logic is what
gets tested, not the integration of the dependencies (those have
their own integration tests).

The market-data adapter is stubbed via a :class:`MarketDataAdapter`
subclass that returns plain dataclasses duck-typing the subset of
:class:`Bar` the cycle reads (``open/high/low/close.as_double()``) —
that keeps the test independent of NautilusTrader Bar construction
while still satisfying the type checker.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

import asyncpg
import pytest

from agora.apps.propfirm.data.nse import MarketDataAdapter, Quote
from agora.apps.propfirm.trading.cycle import run_trading_cycle
from agora.platform.control_plane import pm_provision, pm_repo, trade_repo
from agora.platform.control_plane.pm_repo import PMRecord
from agora.platform.control_plane.trade_repo import PaperTradeRecord, TradeSide
from agora.platform.tools import broker
from agora.platform.tools.broker import BrokerError, OrderResult

# ----- Stubs ---------------------------------------------------------------


@dataclass(frozen=True)
class _FakePrice:
    """Duck-types :class:`nautilus_trader.model.objects.Price`."""

    value: float

    def as_double(self) -> float:
        return self.value


@dataclass(frozen=True)
class _FakeBar:
    """Duck-types the subset of :class:`Bar` that the cycle reads."""

    open: _FakePrice
    high: _FakePrice
    low: _FakePrice
    close: _FakePrice


def _bars_from_closes(closes: list[float]) -> list[_FakeBar]:
    return [
        _FakeBar(
            open=_FakePrice(c),
            high=_FakePrice(c + 0.5),
            low=_FakePrice(c - 0.5),
            close=_FakePrice(c),
        )
        for c in closes
    ]


class _StubMarketData(MarketDataAdapter):
    """Returns canned bars per symbol; raises FileNotFoundError on missing.

    Subclasses :class:`MarketDataAdapter` so the cycle's type signature
    (``MarketDataAdapter | None``) is satisfied. The bars list is
    duck-typed (``_FakeBar``) — the cycle only reads
    ``bar.{open,high,low,close}.as_double()``.
    """

    def __init__(self, bars_by_symbol: dict[str, list[_FakeBar]]) -> None:
        self._bars = bars_by_symbol

    async def bars(self, symbol: str, n: int) -> list[Any]:
        if symbol not in self._bars:
            raise FileNotFoundError(f"no bars for {symbol}")
        return list(self._bars[symbol][-n:])

    async def snapshot(self, symbols: list[str]) -> dict[str, Quote]:
        raise NotImplementedError


def _pm_record(status: str = "running") -> PMRecord:
    return PMRecord(
        id="pm1",
        name="PM1",
        status=status,
        starting_capital_inr=1_000_000.0,
        spawned_at=datetime(2025, 1, 1, tzinfo=UTC),
        stopped_at=None,
        prompt_path="/dev/null",
        config={},
        workflow_id=None,
    )


def _open_trade(
    *,
    trade_id: int = 100,
    symbol: str = "RELIANCE",
    side: TradeSide = "LONG",
    qty: int = 34,
) -> PaperTradeRecord:
    return PaperTradeRecord(
        id=trade_id,
        pm_id="pm1",
        symbol=symbol,
        side=side,
        quantity=qty,
        entry_price=Decimal("1400.00"),
        entry_ts=datetime(2025, 6, 1, tzinfo=UTC),
        stop_loss=None,
        target=None,
        exit_price=None,
        exit_ts=None,
        outcome="open",
        pnl_inr=None,
        pnl_pct=None,
        strategy_id="momentum_v1",
        metadata={},
    )


@pytest.fixture
def patched_repos(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> dict[str, Any]:
    """Patch out every external dependency the cycle reaches for.

    Returns a dict the test reads / mutates to control behaviour and
    inspect what the cycle did. The cycle module imports ``pm_repo``,
    ``trade_repo``, ``broker``, and ``pm_provision`` as modules — so
    monkeypatching the underlying module attribute is enough; the
    cycle's bound references resolve through the same module objects.
    """
    state: dict[str, Any] = {
        "pm": _pm_record(),
        "open_trades": [],  # list[PaperTradeRecord]
        "submit_calls": [],
        "submit_returns": OrderResult(
            trade_id=999,
            pm_id="pm1",
            symbol="RELIANCE",
            side="LONG",
            quantity=34,
            entry_price=Decimal("1400.00"),
            entry_ts=datetime(2025, 6, 15, tzinfo=UTC),
        ),
        "submit_raises": None,  # Optional[BrokerError]
        "close_calls": [],
        "close_raises": None,  # Optional[Exception]
        "workspace_root": tmp_path,
    }

    async def fake_get_pm(_pool: asyncpg.Pool, _pm_id: str) -> PMRecord | None:
        pm: PMRecord | None = state["pm"]
        return pm

    async def fake_list_open_trades(_pool: asyncpg.Pool, _pm_id: str) -> list[PaperTradeRecord]:
        return list(state["open_trades"])

    async def fake_submit_order(_pool: asyncpg.Pool, order: Any, **_: Any) -> OrderResult:
        state["submit_calls"].append(order)
        if state["submit_raises"] is not None:
            raise cast(BrokerError, state["submit_raises"])
        return cast(OrderResult, state["submit_returns"])

    async def fake_close_trade(
        _pool: asyncpg.Pool,
        trade_id: int,
        *,
        exit_price: Decimal,
        exit_ts: datetime,
        outcome: trade_repo.TradeOutcome,
    ) -> PaperTradeRecord:
        state["close_calls"].append(
            {
                "trade_id": trade_id,
                "exit_price": exit_price,
                "exit_ts": exit_ts,
                "outcome": outcome,
            }
        )
        if state["close_raises"] is not None:
            raise cast(Exception, state["close_raises"])
        return PaperTradeRecord(
            id=trade_id,
            pm_id="pm1",
            symbol="RELIANCE",
            side="LONG",
            quantity=34,
            entry_price=Decimal("1400.00"),
            entry_ts=datetime(2025, 6, 1, tzinfo=UTC),
            stop_loss=None,
            target=None,
            exit_price=exit_price,
            exit_ts=exit_ts,
            outcome=outcome,
            pnl_inr=Decimal("-1564.20"),
            pnl_pct=Decimal("-3.28"),
            strategy_id="momentum_v1",
            metadata={},
        )

    monkeypatch.setattr(pm_repo, "get_pm", fake_get_pm)
    monkeypatch.setattr(trade_repo, "list_open_trades", fake_list_open_trades)
    monkeypatch.setattr(trade_repo, "close_trade", fake_close_trade)
    monkeypatch.setattr(broker, "submit_order", fake_submit_order)

    # Pin the workspace root so cycle journal writes land under tmp_path
    # (the cycle calls ``pm_provision.resolve_workspace_root()`` for
    # both watchlist resolution and journal writes via the helper).
    monkeypatch.setattr(pm_provision, "resolve_workspace_root", lambda *a, **kw: tmp_path)

    return state


# ----- Tests ---------------------------------------------------------------


async def test_skips_when_pm_status_is_not_running(
    patched_repos: dict[str, Any],
) -> None:
    """A paused PM must not place orders; result records the rejection."""
    patched_repos["pm"] = _pm_record(status="paused")

    market = _StubMarketData({"RELIANCE": _bars_from_closes([100.0] * 60)})
    result = await run_trading_cycle(
        pool=cast(asyncpg.Pool, None),
        pm_id="pm1",
        watchlist=["RELIANCE"],
        market_data=market,
    )

    assert result.placed == []
    assert result.closed == []
    assert any("paused" in r for r in result.rejected)
    assert patched_repos["submit_calls"] == []


async def test_skips_when_market_data_returns_no_bars(
    patched_repos: dict[str, Any],
) -> None:
    """Empty bars list → SKIPPED for that symbol; no submit/close."""

    class _EmptyAdapter(MarketDataAdapter):
        async def bars(self, _symbol: str, _n: int) -> list[Any]:
            return []

        async def snapshot(self, _symbols: list[str]) -> dict[str, Quote]:
            return {}

    result = await run_trading_cycle(
        pool=cast(asyncpg.Pool, None),
        pm_id="pm1",
        watchlist=["RELIANCE", "TCS"],
        market_data=_EmptyAdapter(),
    )

    assert result.placed == []
    assert set(result.skipped) == {"RELIANCE", "TCS"}
    assert patched_repos["submit_calls"] == []


async def test_places_long_when_signal_fires_and_no_position(
    patched_repos: dict[str, Any],
) -> None:
    """Synthetic uptrend → broker.submit_order is called with the right shape."""
    closes = [100.0 + i * 0.5 for i in range(60)]
    market = _StubMarketData({"RELIANCE": _bars_from_closes(closes)})

    patched_repos["submit_returns"] = OrderResult(
        trade_id=42,
        pm_id="pm1",
        symbol="RELIANCE",
        side="LONG",
        quantity=347,
        entry_price=Decimal(str(closes[-1])),
        entry_ts=datetime(2025, 6, 15, tzinfo=UTC),
    )

    result = await run_trading_cycle(
        pool=cast(asyncpg.Pool, None),
        pm_id="pm1",
        watchlist=["RELIANCE"],
        market_data=market,
    )

    assert result.placed == [42]
    assert result.rejected == []
    assert len(patched_repos["submit_calls"]) == 1
    submitted = patched_repos["submit_calls"][0]
    assert submitted.symbol == "RELIANCE"
    assert submitted.side == "LONG"
    assert submitted.strategy_id == "momentum_v1"
    # Quantity is floor((1_000_000 * 0.05) / last_close) clamped >= 1.
    expected_qty = int(Decimal("50000") // Decimal(str(closes[-1])))
    assert submitted.quantity == expected_qty
    assert submitted.entry_price == Decimal(str(closes[-1]))
    assert submitted.stop_loss is not None and submitted.stop_loss < submitted.entry_price


async def test_skips_long_when_already_in_position(
    patched_repos: dict[str, Any],
) -> None:
    """Open trade for the symbol → cycle treats it as in-position; uptrend
    → NONE (hold), not LONG. broker.submit_order is not called."""
    closes = [100.0 + i * 0.5 for i in range(60)]
    market = _StubMarketData({"RELIANCE": _bars_from_closes(closes)})
    patched_repos["open_trades"] = [_open_trade(symbol="RELIANCE")]

    result = await run_trading_cycle(
        pool=cast(asyncpg.Pool, None),
        pm_id="pm1",
        watchlist=["RELIANCE"],
        market_data=market,
    )

    assert result.placed == []
    assert result.closed == []
    assert result.skipped == ["RELIANCE"]
    assert patched_repos["submit_calls"] == []


async def test_closes_position_on_exit_signal(
    patched_repos: dict[str, Any],
) -> None:
    """Up-then-down regime + open trade → close_trade is called.

    Outcome must be ``'manual'`` per the K3.5 vocabulary mapping note
    (signal-reversal exits don't fit the existing literal set; see
    cycle.py docstring).
    """
    uptrend = [100.0 + i * 1.0 for i in range(60)]
    downtrend = [uptrend[-1] - i * 2.5 for i in range(80)]
    closes = uptrend + downtrend
    market = _StubMarketData({"RELIANCE": _bars_from_closes(closes)})
    patched_repos["open_trades"] = [_open_trade(trade_id=77, symbol="RELIANCE")]

    result = await run_trading_cycle(
        pool=cast(asyncpg.Pool, None),
        pm_id="pm1",
        watchlist=["RELIANCE"],
        market_data=market,
    )

    assert result.closed == [77]
    assert patched_repos["submit_calls"] == []
    assert len(patched_repos["close_calls"]) == 1
    close_args = patched_repos["close_calls"][0]
    assert close_args["trade_id"] == 77
    assert close_args["outcome"] == "manual"
    assert close_args["exit_price"] == Decimal(str(closes[-1]))


async def test_handles_broker_rejection_gracefully(
    patched_repos: dict[str, Any],
) -> None:
    """Broker raises BrokerError → captured in ``rejected``; no exception."""
    closes = [100.0 + i * 0.5 for i in range(60)]
    market = _StubMarketData({"RELIANCE": _bars_from_closes(closes)})
    patched_repos["submit_raises"] = BrokerError("kill switch active")

    result = await run_trading_cycle(
        pool=cast(asyncpg.Pool, None),
        pm_id="pm1",
        watchlist=["RELIANCE"],
        market_data=market,
    )

    assert result.placed == []
    assert any("kill switch active" in r for r in result.rejected)
    # submit_order was attempted (and raised) — that's the path under test.
    assert len(patched_repos["submit_calls"]) == 1


async def test_missing_pm_returns_rejection(
    patched_repos: dict[str, Any],
) -> None:
    """A non-existent PM should not invoke the broker."""
    patched_repos["pm"] = None

    result = await run_trading_cycle(
        pool=cast(asyncpg.Pool, None),
        pm_id="pm1",
        watchlist=["RELIANCE"],
        market_data=_StubMarketData({"RELIANCE": _bars_from_closes([100.0] * 60)}),
    )

    assert result.placed == []
    assert any("not found" in r for r in result.rejected)
    assert patched_repos["submit_calls"] == []
