"""Unit tests for the broker tool's safety-check ordering.

Plan/01-KEYSTONE.md §5 Step 3.4 verification — the kill-switch + PM
state rejection paths. No live Postgres; the safety checks and the
``insert_open_trade`` write are monkeypatched.

Tests cover:

* Successful submission writes a trade and returns the new trade_id.
* Kill switch active → ``BrokerError("kill switch active")``, no insert.
* PM status ``paused`` / ``stopped`` / ``missing`` → ``BrokerError``,
  no insert.
* Kill switch wins over PM state when both fail (security ordering).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from agora.platform.tools import broker
from agora.platform.tools.broker import (
    BrokerError,
    OrderRequest,
    submit_order,
)


def _order(**overrides: Any) -> OrderRequest:
    base: dict[str, Any] = {
        "pm_id": "pm1",
        "symbol": "RELIANCE",
        "side": "LONG",
        "quantity": 10,
        "entry_price": Decimal("1500.00"),
        "stop_loss": Decimal("1450.00"),
        "target": Decimal("1600.00"),
        "strategy_id": "momentum_v1",
    }
    base.update(overrides)
    return OrderRequest(**base)


@pytest.fixture
def insert_recorder(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Capture every ``insert_open_trade`` call without hitting Postgres.

    Returns a dict with ``calls`` (list of kwargs) and ``returns`` (the
    fake trade_id the recorder hands back). The fake id is overrideable
    by mutating ``returns`` in the test body before the call.
    """
    state: dict[str, Any] = {"calls": [], "returns": 42}

    async def fake_insert(_pool: Any, **kwargs: Any) -> int:
        state["calls"].append(kwargs)
        return int(state["returns"])

    monkeypatch.setattr(broker, "insert_open_trade", fake_insert)
    return state


async def test_submit_order_records_trade_when_kill_switch_off(
    insert_recorder: dict[str, Any],
) -> None:
    insert_recorder["returns"] = 42

    async def kill_off(_pool: Any) -> bool:
        return False

    async def pm_running(_pool: Any, _pm_id: str) -> str:
        return "running"

    result = await submit_order(
        pool=None,  # unused — the checks and insert are stubbed
        order=_order(),
        kill_switch_check=kill_off,
        pm_status_check=pm_running,
    )

    assert result.trade_id == 42
    assert result.symbol == "RELIANCE"
    assert result.side == "LONG"
    assert result.quantity == 10
    assert result.entry_price == Decimal("1500.00")
    assert len(insert_recorder["calls"]) == 1
    call = insert_recorder["calls"][0]
    assert call["pm_id"] == "pm1"
    assert call["stop_loss"] == Decimal("1450.00")
    assert call["target"] == Decimal("1600.00")
    assert call["strategy_id"] == "momentum_v1"


async def test_submit_order_rejects_when_kill_switch_active(
    insert_recorder: dict[str, Any],
) -> None:
    async def kill_on(_pool: Any) -> bool:
        return True

    async def pm_running(_pool: Any, _pm_id: str) -> str:
        return "running"

    with pytest.raises(BrokerError, match="kill switch active"):
        await submit_order(
            pool=None,
            order=_order(),
            kill_switch_check=kill_on,
            pm_status_check=pm_running,
        )
    assert insert_recorder["calls"] == []


@pytest.mark.parametrize("status", ["paused", "stopped", "error", "provisioning", "spawned"])
async def test_submit_order_rejects_when_pm_not_running(
    insert_recorder: dict[str, Any],
    status: str,
) -> None:
    async def kill_off(_pool: Any) -> bool:
        return False

    async def pm_check(_pool: Any, _pm_id: str) -> str:
        return status

    with pytest.raises(BrokerError, match=f"is {status}"):
        await submit_order(
            pool=None,
            order=_order(),
            kill_switch_check=kill_off,
            pm_status_check=pm_check,
        )
    assert insert_recorder["calls"] == []


async def test_submit_order_rejects_when_pm_missing(
    insert_recorder: dict[str, Any],
) -> None:
    async def kill_off(_pool: Any) -> bool:
        return False

    async def pm_missing(_pool: Any, _pm_id: str) -> str:
        return "missing"

    with pytest.raises(BrokerError, match="is missing"):
        await submit_order(
            pool=None,
            order=_order(),
            kill_switch_check=kill_off,
            pm_status_check=pm_missing,
        )
    assert insert_recorder["calls"] == []


async def test_kill_switch_check_is_called_first(
    insert_recorder: dict[str, Any],
) -> None:
    """Kill switch wins when both checks would fail.

    Security ordering: a globally-active kill switch must surface
    regardless of per-PM state. The journal entry needs to say
    ``kill switch active`` so an operator sees the right cause.
    """

    async def kill_on(_pool: Any) -> bool:
        return True

    async def pm_paused(_pool: Any, _pm_id: str) -> str:
        return "paused"

    with pytest.raises(BrokerError, match="kill switch active"):
        await submit_order(
            pool=None,
            order=_order(),
            kill_switch_check=kill_on,
            pm_status_check=pm_paused,
        )
    assert insert_recorder["calls"] == []
