"""Tests for B.8 — `core.retry`."""
from __future__ import annotations

import pytest

from core.retry import retry, with_retry


def test_with_retry_returns_value_on_first_success():
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        return 42

    assert with_retry(fn, attempts=3) == 42
    assert calls["n"] == 1


def test_with_retry_recovers_after_failures(monkeypatch):
    monkeypatch.setattr("core.retry._sleep_with_jitter", lambda *a, **kw: None)
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise ConnectionError("nope")
        return "ok"

    assert with_retry(flaky, attempts=5) == "ok"
    assert calls["n"] == 3


def test_with_retry_raises_after_exhausted(monkeypatch):
    monkeypatch.setattr("core.retry._sleep_with_jitter", lambda *a, **kw: None)

    def always_fails():
        raise TimeoutError("doomed")

    with pytest.raises(TimeoutError):
        with_retry(always_fails, attempts=3)


def test_decorator_form(monkeypatch):
    monkeypatch.setattr("core.retry._sleep_with_jitter", lambda *a, **kw: None)
    calls = {"n": 0}

    @retry(attempts=4)
    def flaky():
        calls["n"] += 1
        if calls["n"] < 2:
            raise OSError("net down")
        return "yes"

    assert flaky() == "yes"
    assert calls["n"] == 2


def test_does_not_retry_unlisted_exceptions(monkeypatch):
    monkeypatch.setattr("core.retry._sleep_with_jitter", lambda *a, **kw: None)
    calls = {"n": 0}

    def boom():
        calls["n"] += 1
        raise ValueError("don't retry me")

    with pytest.raises(ValueError):
        with_retry(boom, attempts=5)
    # ValueError isn't in the default retryable set → only one attempt.
    assert calls["n"] == 1
