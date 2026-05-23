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
    load_pm_config,
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
        "build_cycle_seconds:",
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


# --------------------------------------------------------------- load_pm_config
# Plan §10 rule 9 ("one source of truth per piece of state"): the seed
# config.yaml is now actually read on workflow start. K2 wrote it but
# never read it back, so operator edits had no effect. These tests pin
# the contract: missing/malformed → empty dict (caller falls back to
# dataclass defaults), valid YAML → parsed dict.


def test_load_pm_config_missing_returns_empty(tmp_path: Path) -> None:
    """No config.yaml in workspace → empty dict, no error."""
    assert load_pm_config(tmp_path) == {}


def test_load_pm_config_malformed_returns_empty(tmp_path: Path) -> None:
    """Bad YAML logs a warning and returns ``{}`` so the workflow
    falls through to dataclass defaults rather than fail-stopping on
    an operator typo."""
    (tmp_path / "config.yaml").write_text(
        "build_cycle_seconds: : : not yaml\n",
        encoding="utf-8",
    )
    assert load_pm_config(tmp_path) == {}


def test_load_pm_config_top_level_non_mapping_returns_empty(tmp_path: Path) -> None:
    """A YAML file that parses to a list/scalar (not a mapping) is
    treated as malformed — the loader's contract is dict-or-empty."""
    (tmp_path / "config.yaml").write_text("- one\n- two\n", encoding="utf-8")
    assert load_pm_config(tmp_path) == {}


def test_load_pm_config_valid_yaml_returns_parsed_dict(tmp_path: Path) -> None:
    (tmp_path / "config.yaml").write_text(
        "build_cycle_seconds: 7\n" "trading_cycle_seconds: 11\n" "name: PM_Test\n",
        encoding="utf-8",
    )
    cfg = load_pm_config(tmp_path)
    assert cfg["build_cycle_seconds"] == 7
    assert cfg["trading_cycle_seconds"] == 11
    assert cfg["name"] == "PM_Test"


async def test_provisioned_seed_yaml_round_trips_through_load(tmp_path: Path) -> None:
    """End-to-end: the YAML we WROTE at provision time is the YAML we
    READ at workflow start. Catches drift between
    ``_seed_config_yaml`` and ``load_pm_config`` (e.g. the K2 typo
    where the seed wrote ``build_cycle_minutes`` while the dataclass
    field was ``build_cycle_seconds``)."""
    pm_dir = await provision_workspace(
        pm_id="pm1",
        name="PM1",
        starting_capital_inr=1_000_000.0,
        workspace_root=tmp_path,
    )
    cfg = load_pm_config(pm_dir)
    assert cfg.get("build_cycle_seconds") == 60
    assert cfg.get("trading_cycle_seconds") == 60


# ----------------------------------------- provision activity returns cadence
# Lock the contract between the provision activity and the workflow:
# what the workflow sleeps on must come from config.yaml (with seed
# defaults if the file is fresh). The workflow code is hand-tested in
# test_pm_supervisor.py; here we exercise the activity body directly.


async def test_provision_activity_returns_seed_cadence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fresh workspace → provision activity returns the seed defaults
    (60s/60s). This is the production happy path."""
    from agora.platform.workers.pm_supervisor import (
        ProvisionInput,
        provision_pm_workspace,
    )

    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))
    # ``Settings`` is @lru_cache'd — clear the cache so the test env wins.
    from agora.platform.shared import settings as settings_mod

    settings_mod.get_settings.cache_clear()

    result = await provision_pm_workspace(
        ProvisionInput(pm_id="pm1", name="PM1", starting_capital_inr=1.0)
    )
    assert result.build_cycle_seconds == 60
    assert result.trading_cycle_seconds == 60
    assert result.workspace_path.endswith("/pm1")


async def test_provision_activity_honours_operator_edits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pre-existing config.yaml with custom cadence → activity hands
    those values back. This is the K4-edit path: an operator (or PM
    self-reflection) changed config.yaml; the next workflow restart
    picks the new cadence up."""
    from agora.platform.workers.pm_supervisor import (
        ProvisionInput,
        provision_pm_workspace,
    )

    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))
    from agora.platform.shared import settings as settings_mod

    settings_mod.get_settings.cache_clear()

    pm_dir = tmp_path / "pm2"
    pm_dir.mkdir()
    (pm_dir / "config.yaml").write_text(
        "build_cycle_seconds: 7\ntrading_cycle_seconds: 11\n",
        encoding="utf-8",
    )

    result = await provision_pm_workspace(
        ProvisionInput(pm_id="pm2", name="PM2", starting_capital_inr=1.0)
    )
    assert result.build_cycle_seconds == 7
    assert result.trading_cycle_seconds == 11


async def test_provision_activity_falls_back_on_partial_yaml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Partial YAML (missing one cadence key) → fall back to dataclass
    default for the missing key, honour the present one."""
    from agora.platform.workers.pm_supervisor import (
        ProvisionInput,
        provision_pm_workspace,
    )

    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))
    from agora.platform.shared import settings as settings_mod

    settings_mod.get_settings.cache_clear()

    pm_dir = tmp_path / "pm3"
    pm_dir.mkdir()
    # Only build_cycle_seconds set; trading_cycle_seconds missing.
    (pm_dir / "config.yaml").write_text("build_cycle_seconds: 9\n", encoding="utf-8")

    result = await provision_pm_workspace(
        ProvisionInput(pm_id="pm3", name="PM3", starting_capital_inr=1.0)
    )
    assert result.build_cycle_seconds == 9
    assert result.trading_cycle_seconds == 60  # fallback to default
