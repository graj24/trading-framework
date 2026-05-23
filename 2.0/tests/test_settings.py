"""Settings round-trip via env vars."""

from __future__ import annotations

import pytest

from agora.platform.shared.settings import Settings, get_settings


def test_settings_defaults_when_env_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (
        "POSTGRES_URL",
        "TEMPORAL_HOST",
        "LANGFUSE_HOST",
        "LANGFUSE_PUBLIC_KEY",
        "LANGFUSE_SECRET_KEY",
        "LETTA_HOST",
        "QDRANT_HOST",
        "LOG_FORMAT",
        "WORKSPACE_ROOT",
    ):
        monkeypatch.delenv(key, raising=False)

    s = Settings(_env_file=None)
    assert s.postgres_url.startswith("postgresql+asyncpg://")
    assert s.temporal_host == "localhost:7233"
    assert s.langfuse_host == "https://cloud.langfuse.com"
    assert s.langfuse_public_key == ""
    assert s.langfuse_secret_key == ""
    assert s.letta_host == "http://localhost:8283"
    assert s.qdrant_host == "http://localhost:6333"
    assert s.log_format == "human"


def test_settings_overridden_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEMPORAL_HOST", "temporal.svc:9999")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk_test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk_test")
    monkeypatch.setenv("LOG_FORMAT", "json")

    s = Settings(_env_file=None)
    assert s.temporal_host == "temporal.svc:9999"
    assert s.langfuse_public_key == "pk_test"
    assert s.langfuse_secret_key == "sk_test"
    assert s.log_format == "json"


def test_get_settings_is_cached() -> None:
    a = get_settings()
    b = get_settings()
    assert a is b
