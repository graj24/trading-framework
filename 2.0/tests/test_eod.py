"""Unit tests for the K3 Step 3.6 EOD position closer.

The closer reads open trades from ``trade_repo``, snapshots a price
from the market-data adapter, and writes the close back through
``trade_repo.close_trade``. Tests stub the adapter and the repo
helpers so the closer's branching logic (close vs skip) is exercised
without touching Postgres or parquet.

Cases:

* ``test_closes_all_opens`` — three open trades, all priced. All
  three close, none skipped, journal contains the closes.
* ``test_skips_missing_price`` — one trade for a symbol with no
  parquet history. The trade stays open, the closer journals the skip,
  and the result reports the symbol with the reason.
* ``test_partial_failure_mix`` — three trades, one of which the
  market-data snapshot raises on; the other two close cleanly. The
  failed one is reported in ``skipped``; the rest land in ``closed``.
* ``test_writes_summary_line_after_closing_trades`` — the per-run
  SUMMARY rollup line names the right close/skip counts and the
  summed PnL across all closed trades.
* ``test_writes_summary_line_when_no_open_trades`` — an EOD pass
  with zero open positions still writes a zero-valued SUMMARY line
  so the operator can confirm the pass ran.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from agora.apps.propfirm.data.nse import MarketDataAdapter, Quote
from agora.apps.propfirm.trading.eod import (
    EodCloseResult,
    close_positions_for_pm,
)
from agora.platform.control_plane import trade_repo
from agora.platform.control_plane.trade_repo import PaperTradeRecord, TradeSide

# ----- Stubs ---------------------------------------------------------------


class _StubMarketData(MarketDataAdapter):
    """Returns canned :class:`Quote` per symbol; raises on missing.

    A symbol mapped to :class:`FileNotFoundError` (or another exception
    instance) raises that exception on snapshot. Anything not in the
    map returns the default snapshot, mimicking ``ParquetMarketData``'s
    "no parquet -> FileNotFoundError" behaviour.
    """

    def __init__(self, prices: dict[str, float | Exception]) -> None:
        self._prices = prices

    async def snapshot(self, symbols: list[str]) -> dict[str, Quote]:
        out: dict[str, Quote] = {}
        for symbol in symbols:
            value = self._prices.get(symbol)
            if isinstance(value, Exception):
                raise value
            if value is None:
                # Mirror ParquetMarketData's missing-symbol behaviour.
                raise FileNotFoundError(f"no parquet for {symbol}")
            out[symbol] = Quote(
                symbol=symbol,
                price=float(value),
                ts=datetime(2025, 6, 2, tzinfo=UTC),
            )
        return out

    async def bars(self, symbol: str, n: int) -> list[Any]:  # pragma: no cover
        raise NotImplementedError


def _open_trade(
    *,
    trade_id: int,
    symbol: str,
    side: TradeSide = "LONG",
    entry: float = 1400.0,
    qty: int = 10,
) -> PaperTradeRecord:
    return PaperTradeRecord(
        id=trade_id,
        pm_id="pm1",
        symbol=symbol,
        side=side,
        quantity=qty,
        entry_price=Decimal(str(entry)),
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


def _closed_record(
    trade: PaperTradeRecord,
    *,
    exit_price: Decimal,
    outcome: str = "eod_close",
) -> PaperTradeRecord:
    """Return ``trade`` updated with the closer's exit fields.

    Mirrors the math in ``trade_repo.close_trade`` so tests assert the
    same pnl the production code computes.
    """
    assert trade.entry_price is not None
    if trade.side == "LONG":
        pnl = (exit_price - trade.entry_price) * Decimal(trade.quantity)
    else:
        pnl = (trade.entry_price - exit_price) * Decimal(trade.quantity)
    cost = trade.entry_price * Decimal(trade.quantity)
    pct = (pnl / cost) * Decimal(100) if cost != 0 else Decimal(0)
    return trade.model_copy(
        update={
            "exit_price": exit_price,
            "exit_ts": datetime(2025, 6, 2, tzinfo=UTC),
            "outcome": outcome,
            "pnl_inr": pnl,
            "pnl_pct": pct,
        }
    )


@dataclass
class _RepoStubState:
    open_trades: list[PaperTradeRecord]
    closed: list[dict[str, Any]]


@pytest.fixture
def repo_stub(monkeypatch: pytest.MonkeyPatch) -> _RepoStubState:
    """Replace ``trade_repo.list_open_trades`` and ``close_trade``.

    Tests mutate ``state.open_trades`` to control what the closer
    sees, and read ``state.closed`` to assert which trades the closer
    asked the repo to close.
    """
    state = _RepoStubState(open_trades=[], closed=[])

    async def fake_list_open(_pool: Any, pm_id: str) -> list[PaperTradeRecord]:
        assert pm_id == "pm1"
        return list(state.open_trades)

    async def fake_close(
        _pool: Any,
        trade_id: int,
        *,
        exit_price: Decimal,
        exit_ts: datetime,
        outcome: str,
    ) -> PaperTradeRecord:
        # Find the trade we're "closing" and return a closed copy.
        match = next((t for t in state.open_trades if t.id == trade_id), None)
        assert match is not None, f"close_trade called for unknown trade_id={trade_id}"
        state.closed.append(
            {
                "trade_id": trade_id,
                "exit_price": exit_price,
                "exit_ts": exit_ts,
                "outcome": outcome,
            }
        )
        return _closed_record(match, exit_price=exit_price, outcome=outcome)

    monkeypatch.setattr(trade_repo, "list_open_trades", fake_list_open)
    monkeypatch.setattr(trade_repo, "close_trade", fake_close)
    return state


@pytest.fixture
def journal_capture(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> list[str]:
    """Capture every journal_append call into a list.

    The closer writes one journal line per close or skip; tests assert
    on the number of lines and a fragment of each.
    """
    lines: list[str] = []

    def fake_append(pm_id: str, line: str, **_: Any) -> Path:
        assert pm_id == "pm1"
        lines.append(line)
        return tmp_path / "fake-journal.md"

    # Patch the journal symbol the eod module bound at import time.
    import agora.apps.propfirm.trading.eod as eod_module

    monkeypatch.setattr(eod_module, "journal_append", fake_append)
    return lines


# ----- Tests ---------------------------------------------------------------


async def test_closes_all_opens(repo_stub: _RepoStubState, journal_capture: list[str]) -> None:
    """Three open trades, all priced. All three close cleanly."""
    repo_stub.open_trades = [
        _open_trade(trade_id=1, symbol="RELIANCE", entry=1400.0, qty=34),
        _open_trade(trade_id=2, symbol="TCS", entry=3680.0, qty=27),
        _open_trade(trade_id=3, symbol="INFY", entry=1600.0, qty=62),
    ]
    market = _StubMarketData({"RELIANCE": 1421.05, "TCS": 3650.10, "INFY": 1612.30})

    result = await close_positions_for_pm(None, "pm1", market_data=market)

    assert isinstance(result, EodCloseResult)
    assert result.pm_id == "pm1"
    assert sorted(result.closed) == [1, 2, 3]
    assert result.skipped == []

    # close_trade fired three times with outcome='eod_close'.
    assert len(repo_stub.closed) == 3
    for call in repo_stub.closed:
        assert call["outcome"] == "eod_close"

    # Journal got three "CLOSED ..." lines plus the summary rollup.
    closed_lines = [line for line in journal_capture if "CLOSED" in line]
    summary_lines = [line for line in journal_capture if "SUMMARY" in line]
    assert len(closed_lines) == 3
    assert len(summary_lines) == 1


async def test_skips_missing_price(repo_stub: _RepoStubState, journal_capture: list[str]) -> None:
    """A symbol with no parquet stays open and lands in ``skipped``."""
    repo_stub.open_trades = [
        _open_trade(trade_id=1, symbol="MISSING", qty=10),
    ]
    market = _StubMarketData({})  # any symbol -> FileNotFoundError

    result = await close_positions_for_pm(None, "pm1", market_data=market)

    assert result.closed == []
    assert len(result.skipped) == 1
    assert "MISSING" in result.skipped[0]
    assert "no market data" in result.skipped[0]
    # No close_trade call at all.
    assert repo_stub.closed == []
    # Journal got a skip line plus the summary rollup.
    skip_lines = [line for line in journal_capture if "SKIPPED" in line]
    summary_lines = [line for line in journal_capture if "SUMMARY" in line]
    assert len(skip_lines) == 1
    assert len(summary_lines) == 1


async def test_partial_failure_mix(repo_stub: _RepoStubState, journal_capture: list[str]) -> None:
    """Two priced, one raising. Two close, one is skipped."""
    repo_stub.open_trades = [
        _open_trade(trade_id=1, symbol="RELIANCE", entry=1400.0, qty=34),
        _open_trade(trade_id=2, symbol="BROKEN", entry=1000.0, qty=5),
        _open_trade(trade_id=3, symbol="INFY", entry=1600.0, qty=62),
    ]
    market = _StubMarketData(
        {
            "RELIANCE": 1421.05,
            "BROKEN": RuntimeError("upstream feed exploded"),
            "INFY": 1612.30,
        }
    )

    result = await close_positions_for_pm(None, "pm1", market_data=market)

    assert sorted(result.closed) == [1, 3]
    assert len(result.skipped) == 1
    assert "BROKEN" in result.skipped[0]
    # Two close_trade calls; the broken symbol skipped.
    assert {c["trade_id"] for c in repo_stub.closed} == {1, 3}
    # 2 closes + 1 skip + 1 summary rollup = 4 journal lines.
    assert len(journal_capture) == 4
    closed_lines = [line for line in journal_capture if "CLOSED" in line]
    skipped_lines = [line for line in journal_capture if "SKIPPED" in line]
    summary_lines = [line for line in journal_capture if "SUMMARY" in line]
    assert len(closed_lines) == 2
    assert len(skipped_lines) == 1
    assert len(summary_lines) == 1
    assert "BROKEN" in skipped_lines[0]


async def test_writes_summary_line_after_closing_trades(
    repo_stub: _RepoStubState, journal_capture: list[str]
) -> None:
    """SUMMARY line names the right counts and total PnL."""
    repo_stub.open_trades = [
        _open_trade(trade_id=1, symbol="RELIANCE", entry=1400.0, qty=10),
        _open_trade(trade_id=2, symbol="TCS", entry=3000.0, qty=5),
    ]
    market = _StubMarketData({"RELIANCE": 1500.0, "TCS": 2900.0})

    result = await close_positions_for_pm(None, "pm1", market_data=market)

    # closes: (1500-1400)*10 = 1000; (2900-3000)*5 = -500. total = 500.
    assert sorted(result.closed) == [1, 2]
    summary_lines = [line for line in journal_capture if "SUMMARY" in line]
    assert len(summary_lines) == 1
    summary = summary_lines[0]
    assert "[eod]:" in summary
    assert "closed=2" in summary
    assert "skipped=0" in summary
    assert "total_pnl=₹500" in summary


async def test_writes_summary_line_when_no_open_trades(
    repo_stub: _RepoStubState, journal_capture: list[str]
) -> None:
    """Empty open_trades still emits a zero-rollup SUMMARY line.

    Operators rely on the summary to confirm the EOD pass ran. An
    empty pass that wrote nothing would be indistinguishable from a
    failure that crashed before any journal write.
    """
    repo_stub.open_trades = []
    market = _StubMarketData({})

    result = await close_positions_for_pm(None, "pm1", market_data=market)

    assert result.closed == []
    assert result.skipped == []
    assert len(journal_capture) == 1
    summary = journal_capture[0]
    assert "[eod]:" in summary
    assert "SUMMARY closed=0 skipped=0 total_pnl=₹0" in summary
