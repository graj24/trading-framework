"""M-5: ShadowBroker._fill_log must be capped to prevent unbounded growth."""
import pytest
from core.broker import ShadowBroker, PaperBroker

FILL_LOG_MAX = 1000


def _make_broker(monkeypatch):
    broker = ShadowBroker()
    monkeypatch.setattr(broker, "_live", None)
    # Bypass circuit breaker so we can place many orders in the test
    monkeypatch.setattr(broker.paper, "_check_circuit_breaker", lambda: None)
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
