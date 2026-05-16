"""Tests for MED-7 — centralised costs in core/costs.py."""
from __future__ import annotations

import importlib

from core import costs


def test_constants_have_expected_values():
    assert costs.SLIPPAGE_FRAC == 0.0005
    assert costs.BROKERAGE_FRAC == 0.0003
    assert costs.STT_SELL_FRAC == 0.001
    # Round-trip = 2 * 0.0005 + 2 * 0.0003 + 0.001 = 0.0026
    assert abs(costs.ROUND_TRIP_COST_FRAC - 0.0026) < 1e-12


def test_helpers():
    assert abs(costs.cost_per_side(10_000) - 8.0) < 1e-9    # 0.08% of 10k = ₹8
    assert abs(costs.cost_round_trip(10_000) - 26.0) < 1e-9  # 0.26% of 10k = ₹26


def test_execution_agent_uses_centralised_constants():
    """ExecutionAgent must source SLIPPAGE / BROKERAGE from core.costs.
    This guards against future drift back to hard-coded values."""
    from agents import execution_agent
    importlib.reload(execution_agent)
    assert execution_agent.SLIPPAGE == costs.SLIPPAGE_FRAC
    assert execution_agent.BROKERAGE == costs.BROKERAGE_FRAC


def test_backtester_uses_centralised_constants():
    from core import backtester
    importlib.reload(backtester)
    assert backtester.SLIPPAGE == costs.SLIPPAGE_FRAC
    assert backtester.BROKERAGE == costs.BROKERAGE_FRAC


def test_no_orphan_slippage_constants_in_repo():
    """Catch any module that still hard-codes SLIPPAGE = 0.001 or similar.
    We do this with a textual scan because some files (backtest_intraday,
    backtest_gap, simulate_day) are scripts, not importable modules."""
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    bad: list[str] = []
    pattern = re.compile(r"^SLIPPAGE\s*=\s*0\.001\b", re.M)
    for f in root.rglob("*.py"):
        # Skip the canonical module, tests, and the venv.
        rel = f.relative_to(root)
        if any(p in rel.parts for p in (".venv", "tests", "docs-verification")):
            continue
        if rel.name == "costs.py":
            continue
        text = f.read_text(errors="ignore")
        if pattern.search(text):
            bad.append(str(rel))
    assert not bad, f"these files still hard-code SLIPPAGE=0.001: {bad}"
