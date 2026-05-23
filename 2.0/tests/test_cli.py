"""Smoke test for `agora-cli`.

We don't actually run subcommands against a live Temporal cluster. Instead we
verify:
  - the module imports cleanly and exposes the documented entry points,
  - tyro's parser accepts both `hello` and `worker` subcommands,
  - the `hello` subcommand takes `name` positionally (matches the spec).
"""

from __future__ import annotations

import pytest

from agora.platform import cli


def test_main_callable_exists() -> None:
    assert callable(cli.main)
    assert callable(cli.hello)
    assert callable(cli.worker)


def test_tyro_accepts_hello_with_positional_name(monkeypatch: pytest.MonkeyPatch) -> None:
    """`agora-cli hello world` should route to `cli.hello("world")`.

    We patch `_run_hello` (the network-touching coroutine) so the real
    `cli.hello` — including its `Positional[str]` annotation — is exercised.
    """
    seen: dict[str, object] = {}

    async def fake_run_hello(name: str, task_queue: str) -> str:
        seen["name"] = name
        seen["task_queue"] = task_queue
        return f"hello, {name}"

    monkeypatch.setattr(cli, "_run_hello", fake_run_hello)
    monkeypatch.setattr("sys.argv", ["agora-cli", "hello", "world"])

    cli.main()

    assert seen == {"name": "world", "task_queue": "agora"}


def test_tyro_accepts_worker_subcommand(monkeypatch: pytest.MonkeyPatch) -> None:
    """`agora-cli worker` should route to `cli.worker()` without starting one."""
    called: dict[str, object] = {}

    def fake_worker(task_queue: str = "agora") -> None:
        called["task_queue"] = task_queue

    monkeypatch.setattr(cli, "worker", fake_worker)
    monkeypatch.setattr("sys.argv", ["agora-cli", "worker"])

    cli.main()

    assert called == {"task_queue": "agora"}
