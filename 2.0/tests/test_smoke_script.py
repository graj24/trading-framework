"""Tests for ``scripts/smoke_llm.py``.

We unit-test ``main()`` directly (no subprocess) — the script is structured
so its body is a callable. The only path that runs without credentials is
the missing-keys checklist, which must:

  * print each required env var with [missing] / [ok] markers;
  * exit 0 (so ``make smoke-llm`` does not fail in environments without keys).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest

REPO_2_0 = Path(__file__).resolve().parent.parent
SCRIPT = REPO_2_0 / "scripts" / "smoke_llm.py"


def _load_smoke() -> Any:
    """Import ``scripts/smoke_llm.py`` as a module without needing it on PYTHONPATH."""
    spec = importlib.util.spec_from_file_location("smoke_llm_test_target", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_main_exits_zero_when_keys_missing(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    for name in ("ANTHROPIC_API_KEY", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY"):
        monkeypatch.delenv(name, raising=False)

    smoke = _load_smoke()
    rc = smoke.main()
    assert rc == 0

    out = capsys.readouterr().out
    assert "ANTHROPIC_API_KEY" in out
    assert "LANGFUSE_PUBLIC_KEY" in out
    assert "LANGFUSE_SECRET_KEY" in out
    assert "[missing]" in out


def test_main_lists_partial_misses(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-anthropic-fake")
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)

    smoke = _load_smoke()
    rc = smoke.main()
    assert rc == 0

    out = capsys.readouterr().out
    # ANTHROPIC is set, the other two are not.
    assert "[     ok] ANTHROPIC_API_KEY" in out
    assert "[missing] LANGFUSE_PUBLIC_KEY" in out
    assert "[missing] LANGFUSE_SECRET_KEY" in out
