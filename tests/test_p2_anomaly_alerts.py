"""Tests for anomaly alerts: '0 pre-open results' and 'P&L close to limit'."""
from __future__ import annotations

from unittest.mock import MagicMock, patch
import pytest


def _config(capital=10000, max_loss_pct=3.0):
    return {
        "trading": {"capital": capital, "mode": "paper"},
        "risk": {"max_loss_per_day_pct": max_loss_pct},
        "watchlist": [],
    }


# ── pre-open zero-results alert ───────────────────────────────────────────────

class TestPreOpenZeroResultsAlert:
    def _run(self, scan_result):
        """Run job_preopen_scan with mocked dependencies; return sent messages."""
        import core.scheduler as sched

        sent = []
        alerter = MagicMock()
        alerter.send.side_effect = sent.append

        monitor = MagicMock()
        monitor.scan.return_value = scan_result

        earnings = MagicMock()
        earnings.morning_scan.return_value = {}

        with (
            patch.object(sched, "_load_config", return_value=_config()),
            # Patch the classes where they are defined (lazy-imported inside the job)
            patch("agents.pre_open_monitor.PreOpenMonitor", return_value=monitor),
            patch("agents.earnings_calendar_agent.EarningsCalendarAgent", return_value=earnings),
            patch("core.alerts.TelegramAlerter", return_value=alerter),
        ):
            # Re-import inside patch context so lazy imports pick up mocks
            import importlib
            import core.scheduler as s2
            # Directly call the job function with patched globals
            _orig_alerter = None
            # Simplest approach: call the job and intercept via the alerter mock
            # The job does `from core.alerts import TelegramAlerter` lazily,
            # so we patch at the source.
            sched.job_preopen_scan()

        return sent

    def _run_patched(self, scan_result):
        """Patch all lazy imports used by job_preopen_scan."""
        import core.scheduler as sched

        sent = []
        alerter_inst = MagicMock()
        alerter_inst.send.side_effect = sent.append

        monitor_inst = MagicMock()
        monitor_inst.scan.return_value = scan_result

        earnings_inst = MagicMock()
        earnings_inst.morning_scan.return_value = {}

        with (
            patch.object(sched, "_load_config", return_value=_config()),
            patch("core.scheduler.PreOpenMonitor", monitor_inst.__class__, create=True),
        ):
            pass  # just checking

        # Use sys.modules patching for lazy imports
        import sys
        import types

        fake_pre_open = types.ModuleType("agents.pre_open_monitor")
        fake_pre_open.PreOpenMonitor = lambda cfg: monitor_inst
        fake_earnings = types.ModuleType("agents.earnings_calendar_agent")
        fake_earnings.EarningsCalendarAgent = lambda cfg: earnings_inst
        fake_alerts = types.ModuleType("core.alerts")
        fake_alerts.TelegramAlerter = lambda: alerter_inst

        orig = {k: sys.modules.get(k) for k in [
            "agents.pre_open_monitor",
            "agents.earnings_calendar_agent",
            "core.alerts",
        ]}
        sys.modules["agents.pre_open_monitor"] = fake_pre_open
        sys.modules["agents.earnings_calendar_agent"] = fake_earnings
        sys.modules["core.alerts"] = fake_alerts
        try:
            with patch.object(sched, "_load_config", return_value=_config()):
                with patch("core.holidays.is_trading_day", return_value=True):
                    sched.job_preopen_scan()
        finally:
            for k, v in orig.items():
                if v is None:
                    sys.modules.pop(k, None)
                else:
                    sys.modules[k] = v

        return sent

    def test_zero_results_sends_anomaly_alert(self):
        msgs = self._run_patched({"all_preopen": {}, "buy_signals": [], "avoid_signals": []})
        assert any("ANOMALY" in m for m in msgs), f"Expected ANOMALY alert, got: {msgs}"

    def test_normal_results_no_anomaly_alert(self):
        msgs = self._run_patched({
            "all_preopen": {"RELIANCE": {"gap_pct": 0.5}},
            "buy_signals": [],
            "avoid_signals": [],
        })
        assert not any("ANOMALY" in m for m in msgs), f"Unexpected ANOMALY: {msgs}"

    def test_buy_signal_sent_when_present(self):
        msgs = self._run_patched({
            "all_preopen": {"TCS": {"gap_pct": 3.0}},
            "buy_signals": [{
                "symbol": "TCS", "gap_pct": 3.0, "reasoning": "test",
                "entry": 100, "stop_loss": 95, "target": 110,
            }],
            "avoid_signals": [],
        })
        assert any("TCS" in m for m in msgs)


# ── P&L close to limit alert ──────────────────────────────────────────────────

class TestPnlLimitAlert:
    def _run_patched(self, pnl_pct: float, cfg: dict):
        import core.scheduler as sched
        import sys, types

        sent = []
        alerter_inst = MagicMock()
        alerter_inst.send.side_effect = sent.append
        alerter_inst.exit_alert = MagicMock()

        executor_inst = MagicMock()
        executor_inst.monitor_positions.return_value = []

        news_inst = MagicMock()
        news_inst.monitor_open_positions.return_value = {}

        fake_exec = types.ModuleType("agents.execution_agent")
        fake_exec.ExecutionAgent = lambda cfg: executor_inst
        fake_exec.today_pnl_pct = lambda capital, db_path=None: pnl_pct

        fake_news = types.ModuleType("agents.news_agent")
        fake_news.NewsAgent = lambda cfg: news_inst

        fake_alerts = types.ModuleType("core.alerts")
        fake_alerts.TelegramAlerter = lambda: alerter_inst

        fake_watchlist = types.ModuleType("core.watchlist")
        fake_watchlist.resolve_watchlist = lambda cfg: []

        keys = ["agents.execution_agent", "agents.news_agent",
                "core.alerts", "core.watchlist"]
        orig = {k: sys.modules.get(k) for k in keys}
        sys.modules["agents.execution_agent"] = fake_exec
        sys.modules["agents.news_agent"] = fake_news
        sys.modules["core.alerts"] = fake_alerts
        sys.modules["core.watchlist"] = fake_watchlist
        try:
            with patch.object(sched, "_load_config", return_value=cfg):
                # Patch today_pnl_pct at the scheduler level since it's imported lazily
                with patch("core.scheduler.today_pnl_pct", lambda capital, db_path=None: pnl_pct):
                    sched.job_monitor_positions()
        finally:
            for k, v in orig.items():
                if v is None:
                    sys.modules.pop(k, None)
                else:
                    sys.modules[k] = v

        return sent

    def test_alert_fires_at_75_pct_of_limit(self):
        # 75% of 4% = 3% → pnl of -3.0 should trigger
        msgs = self._run_patched(-3.0, _config(max_loss_pct=4.0))
        assert any("P&L" in m or "ALERT" in m for m in msgs), msgs

    def test_no_alert_when_pnl_ok(self):
        msgs = self._run_patched(-1.0, _config(max_loss_pct=3.0))
        assert not any("P&L" in m or "ALERT" in m for m in msgs), msgs

    def test_no_alert_when_pnl_positive(self):
        msgs = self._run_patched(1.5, _config(max_loss_pct=3.0))
        assert not any("P&L" in m or "ALERT" in m for m in msgs), msgs

    def test_alert_threshold_uses_config_limit(self):
        # 75% of 1% = 0.75% → -0.8 should trigger
        msgs = self._run_patched(-0.8, _config(max_loss_pct=1.0))
        assert any("P&L" in m or "ALERT" in m for m in msgs), msgs
