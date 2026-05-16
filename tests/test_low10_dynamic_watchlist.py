"""Tests for LOW-10 — dynamic watchlist lives in data/dynamic_watchlist.json,
not in config.yaml.

Goals:
* `core/watchlist.py:resolve_watchlist(config, dynamic_path)` returns
  the merged effective watchlist (`core_watchlist + dynamic`, deduped,
  capped at `watchlist_max`).
* `core/watchlist.py:add_to_dynamic_watchlist(symbols, dynamic_path)`
  appends to the JSON file without ever touching config.yaml.
* `DiscoveryAgent` and `PreOpenMonitor` use the new helper instead of
  `_add_to_watchlist` (which mutated config.yaml and lost comments).
"""
from __future__ import annotations

import json
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def _stub_heavy_imports(monkeypatch):
    fake_pipe = MagicMock(return_value=[[{"label": "POSITIVE", "score": 0.5}]])
    fake_transformers = types.ModuleType("transformers")
    fake_transformers.pipeline = lambda *a, **kw: fake_pipe  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "transformers", fake_transformers)


# ── core/watchlist.py helpers ────────────────────────────────────────────────

def test_resolve_watchlist_merges_core_and_dynamic(tmp_path):
    from core.watchlist import resolve_watchlist

    config = {
        "core_watchlist": ["RELIANCE", "INFY"],
        "watchlist_max": 5,
    }
    dyn = tmp_path / "dynamic_watchlist.json"
    dyn.write_text(json.dumps(["TCS", "HDFCBANK"]))

    eff = resolve_watchlist(config, dyn)
    # Core preserved order, then dynamic, no duplicates.
    assert eff == ["RELIANCE", "INFY", "TCS", "HDFCBANK"]


def test_resolve_watchlist_dedupes(tmp_path):
    from core.watchlist import resolve_watchlist

    config = {"core_watchlist": ["RELIANCE", "INFY"], "watchlist_max": 5}
    dyn = tmp_path / "d.json"
    dyn.write_text(json.dumps(["RELIANCE", "TCS"]))  # RELIANCE duplicated

    eff = resolve_watchlist(config, dyn)
    assert eff.count("RELIANCE") == 1
    assert "TCS" in eff


def test_resolve_watchlist_caps_at_max(tmp_path):
    from core.watchlist import resolve_watchlist

    config = {
        "core_watchlist": ["A", "B", "C"],
        "watchlist_max": 4,
    }
    dyn = tmp_path / "d.json"
    dyn.write_text(json.dumps(["D", "E", "F", "G"]))

    eff = resolve_watchlist(config, dyn)
    assert len(eff) == 4
    # Core stays — they're priority.
    assert eff[:3] == ["A", "B", "C"]


def test_resolve_watchlist_handles_missing_dynamic_file(tmp_path):
    from core.watchlist import resolve_watchlist

    config = {"core_watchlist": ["RELIANCE"], "watchlist_max": 5}
    eff = resolve_watchlist(config, tmp_path / "nope.json")
    assert eff == ["RELIANCE"]


def test_resolve_watchlist_falls_back_to_legacy_watchlist(tmp_path):
    """If neither core_watchlist nor a dynamic file exist, fall back to the
    legacy `watchlist` config key for backwards compatibility."""
    from core.watchlist import resolve_watchlist

    config = {
        "watchlist": ["RELIANCE", "TCS"],
        "watchlist_max": 5,
    }
    eff = resolve_watchlist(config, tmp_path / "missing.json")
    assert eff == ["RELIANCE", "TCS"]


def test_add_to_dynamic_watchlist_creates_file(tmp_path):
    from core.watchlist import add_to_dynamic_watchlist

    dyn = tmp_path / "dynamic_watchlist.json"
    added = add_to_dynamic_watchlist(["TCS", "INFY"], dyn)
    assert sorted(added) == ["INFY", "TCS"]
    assert json.loads(dyn.read_text()) == ["TCS", "INFY"]


def test_add_to_dynamic_watchlist_appends_unique(tmp_path):
    from core.watchlist import add_to_dynamic_watchlist

    dyn = tmp_path / "d.json"
    dyn.write_text(json.dumps(["TCS"]))

    added = add_to_dynamic_watchlist(["TCS", "INFY"], dyn)
    assert added == ["INFY"]   # only the genuinely new one
    assert json.loads(dyn.read_text()) == ["TCS", "INFY"]


def test_add_to_dynamic_watchlist_does_not_touch_config_yaml(tmp_path, monkeypatch):
    """Verify the helper does NOT write to a config.yaml even if one exists
    next to the dynamic file. The whole point of LOW-10 is that config.yaml
    is treated as read-only by the daemon."""
    from core.watchlist import add_to_dynamic_watchlist

    # Pretend config.yaml exists in the same dir.
    cfg = tmp_path / "config.yaml"
    cfg.write_text("watchlist:\n  - RELIANCE\n# preserved comment\n")

    add_to_dynamic_watchlist(["INFY"], tmp_path / "dynamic_watchlist.json")
    # config.yaml content unchanged.
    assert "preserved comment" in cfg.read_text()


# ── DiscoveryAgent / PreOpenMonitor use the new helper ──────────────────────

def test_discovery_agent_writes_to_dynamic_file(tmp_path, monkeypatch):
    """`DiscoveryAgent._add_to_watchlist` must write to data/dynamic_watchlist.json,
    not config.yaml."""
    monkeypatch.chdir(tmp_path)
    # Minimal config so the agent doesn't crash on missing keys.
    (tmp_path / "config.yaml").write_text(
        "watchlist:\n  - RELIANCE\ncore_watchlist:\n  - RELIANCE\nwatchlist_max: 20\n"
    )

    from agents.discovery_agent import DiscoveryAgent
    agent = DiscoveryAgent({"watchlist": ["RELIANCE"], "core_watchlist": ["RELIANCE"], "watchlist_max": 20})
    added = agent._add_to_watchlist(["TCS", "INFY"])
    assert sorted(added) == ["INFY", "TCS"]

    dyn = tmp_path / "data" / "dynamic_watchlist.json"
    assert dyn.exists(), "dynamic file should have been created"
    assert sorted(json.loads(dyn.read_text())) == ["INFY", "TCS"]

    # config.yaml must NOT have been rewritten with TCS/INFY.
    cfg = (tmp_path / "config.yaml").read_text()
    assert "TCS" not in cfg
    assert "INFY" not in cfg
