"""Tests for B.4 — `core/config.py` singleton."""
from __future__ import annotations

from pathlib import Path

import pytest


def test_loads_real_config(monkeypatch):
    """Real config.yaml in the repo loads cleanly with merged defaults."""
    from core import config as cfg
    cfg.set_config(None)  # clear any test override
    out = cfg.get_config()
    # Real config has these keys; defaults fill anything missing.
    assert "trading" in out
    assert "risk" in out
    assert "watchlist_max" in out
    # Default merge must populate trading.mode if missing in disk file.
    assert out["trading"].get("mode") in ("paper", "live")


def test_set_config_overrides_get(tmp_path):
    from core import config as cfg
    custom = {"trading": {"mode": "paper", "capital": 999}, "watchlist": []}
    cfg.set_config(custom)
    try:
        assert cfg.get_config()["trading"]["capital"] == 999
    finally:
        cfg.set_config(None)


def test_loads_from_alternate_path(tmp_path):
    from core import config as cfg
    alt = tmp_path / "alt.yaml"
    alt.write_text("trading:\n  capital: 12345\n")
    cfg.set_config(None)
    out = cfg.get_config(alt)
    assert out["trading"]["capital"] == 12345


def test_missing_file_returns_defaults(tmp_path):
    from core import config as cfg
    cfg.set_config(None)
    out = cfg.get_config(tmp_path / "does-not-exist.yaml")
    # Defaults populated.
    assert out["trading"]["mode"] == "paper"
    assert out["watchlist_max"] == 20
    assert out["risk"]["kelly_fraction"] == 0.5
