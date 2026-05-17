"""Test for MED-6 — scheduler intraday gate uses naive datetime.now().

The bug: `core/scheduler.py:job_intraday_scan` checks
``9*60+15 <= now.hour*60 + now.minute <= 15*60`` using naive
``datetime.now()``. On a non-IST machine that's wrong.

Fix: use ``datetime.now(tz=ZoneInfo("Asia/Kolkata"))`` and compare
in IST.

We can't easily monkeypatch the cron-trigger fire time, but we CAN
patch ``datetime.now`` and ``zoneinfo.ZoneInfo`` and verify the
function's gate decision is timezone-aware.
"""
from __future__ import annotations

import datetime as _dt
from zoneinfo import ZoneInfo

import pytest


@pytest.fixture
def stub_no_op(monkeypatch):
    """Replace every external call inside job_intraday_scan with a no-op
    so the test focuses purely on the time-gate."""
    from core import scheduler

    class _Noop:
        def __getattr__(self, _name):
            return lambda *a, **kw: None

    # The function lazily imports IntradayPatternScanner / ExecutionAgent /
    # TelegramAlerter — it's a try/except, so we don't need to stub.
    yield


def _call_gate(now_obj, monkeypatch):
    """Run job_intraday_scan with `datetime.now()` returning the given naive
    or aware datetime; capture whether the gate let execution past."""
    from core import scheduler

    sentinel = {"reached": False}

    def _stub_scanner_module(*_a, **_kw):
        sentinel["reached"] = True
        # Raise so the function's outer try/except logs and returns.
        raise RuntimeError("stub")

    real_dt = scheduler.datetime

    class _FakeDt(real_dt):
        @classmethod
        def now(cls, tz=None):
            if tz is None:
                # Replicate the timezone-naive behaviour.
                return now_obj.replace(tzinfo=None) if now_obj.tzinfo else now_obj
            return now_obj.astimezone(tz) if now_obj.tzinfo else now_obj.replace(tzinfo=tz)

    monkeypatch.setattr(scheduler, "datetime", _FakeDt)

    # Stub the module-level import the function does:
    import sys
    import types

    fake_mod = types.ModuleType("agents.intraday_scanner")
    fake_mod.IntradayPatternScanner = _stub_scanner_module  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "agents.intraday_scanner", fake_mod)

    scheduler.job_intraday_scan()
    return sentinel["reached"]


def test_gate_passes_during_market_hours_in_ist(monkeypatch):
    """10:00 IST (= 04:30 UTC) must let the gate through."""
    ist = ZoneInfo("Asia/Kolkata")
    now_ist = _dt.datetime(2025, 5, 16, 10, 0, 0, tzinfo=ist)
    reached = _call_gate(now_ist, monkeypatch)
    assert reached, "gate should pass at 10:00 IST"


def test_gate_blocks_outside_market_hours_in_ist(monkeypatch):
    """22:00 IST must NOT let the gate through, even if the local naive
    clock would say something inside [09:15, 15:00]."""
    ist = ZoneInfo("Asia/Kolkata")
    now_ist = _dt.datetime(2025, 5, 16, 22, 0, 0, tzinfo=ist)
    reached = _call_gate(now_ist, monkeypatch)
    assert not reached, "gate should block at 22:00 IST"


def test_gate_uses_ist_when_running_on_utc_machine(monkeypatch):
    """05:00 UTC corresponds to 10:30 IST — gate must pass.
    A naive ``datetime.now()`` on a UTC server would have said 05:00 → blocked.
    The fixed code must read 10:30 → pass.
    """
    utc = ZoneInfo("UTC")
    now_utc = _dt.datetime(2025, 5, 16, 5, 0, 0, tzinfo=utc)
    reached = _call_gate(now_utc, monkeypatch)
    assert reached, "gate should pass when 05:00 UTC == 10:30 IST"
