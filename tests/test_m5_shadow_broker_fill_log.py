"""M-5: ShadowBroker._fill_log must be capped to prevent unbounded growth."""
import pytest
from core.broker import ShadowBroker, PaperBroker

FILL_LOG_MAX = 1000


def _make_broker(monkeypatch):
    from core.broker import _reset_rate_limiters
    _reset_rate_limiters()
    broker = ShadowBroker()
    monkeypatch.setattr(broker, "_live", None)
    # Bypass both circuit breakers so we can place many orders in the test
    monkeypatch.setattr(broker.paper, "_check_circuit_breaker", lambda: None)
    import common.core.broker as _broker_mod
    monkeypatch.setattr(_broker_mod, "_check_global_rate_limit", lambda pm_id="": None)
    return broker


def test_fill_log_capped_at_max(monkeypatch):
    broker = _make_broker(monkeypatch)

    for i in range(FILL_LOG_MAX + 50):
        broker.place_order("RELIANCE", qty=1, tag=f"t{i}")

    assert len(broker._fill_log) <= FILL_LOG_MAX


def test_fill_log_retains_latest(monkeypatch):
    """After overflow, the most recent entries must be kept."""
    broker = _make_broker(monkeypatch)

    for i in range(FILL_LOG_MAX + 10):
        broker.place_order("RELIANCE", qty=1, tag=f"t{i}")

    assert broker._fill_log[-1]["symbol"] == "RELIANCE"
