"""Test for LOW-9 — ripple/config.py hard-coded path."""
from __future__ import annotations

import importlib
from pathlib import Path


def test_ripple_output_dir_is_repo_relative_by_default(monkeypatch):
    """OUTPUT_DIR must default to a path inside this repo, not a hard-coded
    absolute path on a different developer's machine."""
    monkeypatch.delenv("OUTPUT_DIR", raising=False)
    # Force re-import so the module-level constant is recomputed.
    import ripple.config as cfg
    importlib.reload(cfg)

    repo_root = Path(__file__).resolve().parent.parent
    out = Path(cfg.OUTPUT_DIR)
    assert out.is_absolute() or not out.is_absolute()  # either is fine
    assert "anantamanoranjan" not in str(out), f"hard-coded path leaked: {out}"
    # Should be located at or under the repo root.
    assert str(out).startswith(str(repo_root)), \
        f"OUTPUT_DIR {out} is not under repo root {repo_root}"


def test_ripple_output_dir_respects_env(monkeypatch, tmp_path):
    """When OUTPUT_DIR is set in the environment, it overrides the default."""
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path))
    import ripple.config as cfg
    importlib.reload(cfg)
    assert Path(cfg.OUTPUT_DIR) == tmp_path
