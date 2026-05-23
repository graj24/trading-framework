"""Pure-Python unit tests for PM workspace provisioning.

Idempotency is the load-bearing property: the PMSupervisor workflow
will re-run the provision activity on every restart per Temporal's
at-least-once contract. If any of these tests start failing, K2 Step
2.2 will silently overwrite PM journals/plans on every restart.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agora.platform.control_plane.pm_provision import (
    provision_workspace,
    resolve_workspace_root,
)


async def test_creates_directory_tree(tmp_path: Path) -> None:
    pm_dir = await provision_workspace(
        pm_id="pm1",
        name="PM1",
        starting_capital_inr=1_000_000.0,
        workspace_root=tmp_path,
    )
    assert pm_dir == (tmp_path / "pm1").resolve()
    for sub in ("plans", "journals", "strategies", "research", "code"):
        assert (pm_dir / sub).is_dir(), f"missing directory: {sub}"
    assert (pm_dir / "plans" / "current.md").is_file()
    # journals/<today>.md exists; we assert the file is present without
    # pinning the exact filename so this isn't flaky across midnight UTC.
    journals = list((pm_dir / "journals").glob("*.md"))
    assert len(journals) == 1
    assert (pm_dir / "config.yaml").is_file()


async def test_idempotent_skip_existing(tmp_path: Path) -> None:
    """Re-provisioning a workspace must not clobber existing files."""
    # Pre-populate with custom content the agent might have written.
    pm_dir = tmp_path / "pm1"
    (pm_dir / "plans").mkdir(parents=True)
    custom = "PM has been thinking. Please do not erase me.\n"
    (pm_dir / "plans" / "current.md").write_text(custom, encoding="utf-8")

    await provision_workspace(
        pm_id="pm1",
        name="PM1",
        starting_capital_inr=1_000_000.0,
        workspace_root=tmp_path,
    )
    # Custom plan content is preserved.
    assert (pm_dir / "plans" / "current.md").read_text(encoding="utf-8") == custom
    # All siblings now exist.
    for sub in ("journals", "strategies", "research", "code"):
        assert (pm_dir / sub).is_dir()


async def test_seed_files_have_expected_content(tmp_path: Path) -> None:
    pm_dir = await provision_workspace(
        pm_id="pm1",
        name="PM1",
        starting_capital_inr=1_500_000.0,
        workspace_root=tmp_path,
    )
    plan = (pm_dir / "plans" / "current.md").read_text(encoding="utf-8")
    assert plan.startswith("PM PM1 initialized at "), plan

    config = (pm_dir / "config.yaml").read_text(encoding="utf-8")
    # Don't pin exact YAML formatting; assert each key appears.
    for key in (
        "name:",
        "spawned_at:",
        "starting_capital_inr:",
        "build_cycle_minutes:",
        "trading_cycle_seconds:",
        "daily_budget_usd_build:",
        "daily_budget_usd_trading:",
        "default_reasoning_model:",
        "default_cheap_model:",
    ):
        assert key in config, f"missing key {key!r} in config.yaml"
    assert "1500000" in config


@pytest.mark.parametrize(
    "bad_id",
    [
        "../escape",
        "pm/1",
        "PM1",  # uppercase
        "1pm",  # leading digit
        "",  # empty
        "p" * 33,  # too long
        "pm-1",  # hyphen not in alphabet (endpoint converts to underscore first)
    ],
)
async def test_invalid_pm_id_rejected(tmp_path: Path, bad_id: str) -> None:
    """Path-traversal guard: bad pm_id raises ValueError before any I/O."""
    with pytest.raises(ValueError):
        await provision_workspace(
            pm_id=bad_id,
            name="x",
            starting_capital_inr=1.0,
            workspace_root=tmp_path,
        )


def test_resolve_workspace_root_prefers_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    from agora.platform.shared.settings import Settings

    s = Settings(_env_file=None, workspace_root="/tmp/agora-test-workspace")
    assert resolve_workspace_root(s) == Path("/tmp/agora-test-workspace").resolve()


def test_resolve_workspace_root_defaults_under_repo() -> None:
    """Empty setting → ``<repo-2.0>/pms``."""
    from agora.platform.shared.settings import Settings

    s = Settings(_env_file=None, workspace_root="")
    root = resolve_workspace_root(s)
    assert root.name == "pms"
    assert root.parent.name == "2.0"
