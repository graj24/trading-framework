"""Tests for Phase 1 & 2: common/ imports, universe, data sources, strategy registry, PM workspace."""
import pytest
import tempfile
from pathlib import Path


# ── Phase 1: common imports ───────────────────────────────────────────────────

def test_common_core_imports():
    from common.core.event_bus import get_bus
    from common.core.config import get_config
    from common.core.symbols import NIFTY_50
    from common.core.broker import PaperBroker
    assert len(NIFTY_50) == 50
    assert get_bus() is not None


def test_shim_imports_still_work():
    """Backward-compat: old import paths must resolve."""
    from core.event_bus import get_bus
    from core.config import get_config
    from core.symbols import NIFTY_50
    from agents.base import Agent
    assert len(NIFTY_50) == 50


def test_shim_private_attrs_accessible():
    """Shims must expose private names (tests access them directly)."""
    import core.bse_scrip as m
    assert hasattr(m, "_ensure_loaded")
    import core.retry as r
    assert hasattr(r, "_sleep_with_jitter")
    import agents.execution_agent as e
    assert hasattr(e, "_get_ltp")


# ── Phase 2: NSE universe ─────────────────────────────────────────────────────

def test_universe_loads():
    from common.universe import load_nse_universe
    universe = load_nse_universe()
    assert len(universe) > 100, f"Expected >100 symbols, got {len(universe)}"


def test_universe_contains_nifty50():
    from common.universe import get_symbol
    sym = get_symbol("RELIANCE")
    assert sym is not None
    assert sym.symbol == "RELIANCE"


def test_universe_filter_by_tier():
    from common.universe import find_symbols
    large = find_symbols(tier="large")
    assert len(large) > 0
    assert all(s.market_cap_tier == "large" for s in large)


def test_universe_deduplication():
    from common.universe import load_nse_universe
    universe = load_nse_universe()
    symbols = [s.symbol for s in universe]
    assert len(symbols) == len(set(symbols)), "Duplicate symbols in universe"


# ── Phase 2: DataSource ───────────────────────────────────────────────────────

def test_datasource_registry():
    from common.data_sources import get_source, register_source, YFinanceSource
    src = get_source("yfinance")
    assert src.name == "yfinance"


def test_custom_datasource_registration():
    from common.data_sources import DataSource, Quote, register_source, get_source

    class StubSource(DataSource):
        @property
        def name(self):
            return "stub_test"

        def get_quote(self, symbol):
            return Quote(symbol=symbol, ltp=999.0)

    register_source(StubSource())
    src = get_source("stub_test")
    q = src.get_quote("RELIANCE")
    assert q.ltp == 999.0
    assert q.symbol == "RELIANCE"


def test_not_supported_raises():
    from common.data_sources import YFinanceSource, NotSupported
    src = YFinanceSource()
    with pytest.raises(NotSupported):
        src.get_news("RELIANCE")


# ── Phase 2: Strategy registry ────────────────────────────────────────────────

def test_strategy_commit_and_load(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from common.strategy.registry import commit_new_version, load_active, get_active_version

    strategy = {"name": "test", "watchlist": ["RELIANCE"], "gates": {"min_score": 6}}
    v = commit_new_version("99", strategy, notes="test strategy")
    assert v == 1

    active = load_active("99")
    assert active is not None
    assert active["name"] == "test"
    assert get_active_version("99") == 1


def test_strategy_versioning(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from common.strategy.registry import commit_new_version, list_versions, set_active_version, get_active_version

    v1 = commit_new_version("88", {"name": "v1"}, notes="first")
    v2 = commit_new_version("88", {"name": "v2"}, notes="second")
    assert v1 == 1
    assert v2 == 2

    versions = list_versions("88")
    assert len(versions) == 2
    assert versions[0]["version"] == 2  # newest first

    set_active_version("88", 1)
    assert get_active_version("88") == 1


def test_strategy_diff(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from common.strategy.registry import commit_new_version, diff

    commit_new_version("77", {"name": "v1", "watchlist": ["RELIANCE"]})
    commit_new_version("77", {"name": "v2", "watchlist": ["RELIANCE", "TCS"]})

    d = diff("77", 1, 2)
    assert "v001.yaml" in d
    assert "v002.yaml" in d
    assert "TCS" in d


# ── Phase 2: PM workspace bootstrap ──────────────────────────────────────────

def test_register_pm_creates_layout(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from common.core.pm_runtime import register_pm

    ws = register_pm("test99")
    assert (ws / "state" / "plan.md").exists()
    assert (ws / "state" / "positions.json").exists()
    assert (ws / "strategies" / "ACTIVE").exists()
    assert (ws / "strategies" / "v001.yaml").exists()
    assert (ws / "watchlist.yaml").exists()
    assert (ws / "agents" / "__init__.py").exists()


def test_register_pm_copy_from(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from common.core.pm_runtime import register_pm
    from common.strategy.registry import commit_new_version, load_active

    # Register source PM with a real strategy
    register_pm("src1")
    commit_new_version("src1", {"name": "momentum", "watchlist": ["RELIANCE"]}, notes="source strategy")

    # Register target PM copying from source
    register_pm("tgt2", copy_from="src1")
    active = load_active("tgt2")
    assert active is not None
    # Should have copied the strategy
    assert "momentum" in str(active) or "copy" in active.get("description", "").lower()
