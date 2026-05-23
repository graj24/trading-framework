"""Unit tests for ``compute_cost_usd``.

litellm.cost_per_token is monkey-patched so these tests run without network
or any model-pricing-map lookup. The two real codepaths to cover:

  * known model -> sums the (prompt_cost, completion_cost) tuple in USD;
  * unknown / raising -> caught, warned, returns 0.0.

Plus a defensive case for the ``usage`` shape (dict vs object).
"""

from __future__ import annotations

from typing import Any

import litellm
import pytest

from agora.platform.llm.cost import compute_cost_usd


def test_known_model_returns_positive_cost(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_cost_per_token(**kwargs: Any) -> tuple[float, float]:
        captured.update(kwargs)
        return (0.0001, 0.0002)

    monkeypatch.setattr(litellm, "cost_per_token", fake_cost_per_token)

    class FakeUsage:
        prompt_tokens = 100
        completion_tokens = 50

    total = compute_cost_usd(usage=FakeUsage(), model="anthropic/claude-sonnet-4-5")
    assert total == pytest.approx(0.0003)
    assert captured["model"] == "anthropic/claude-sonnet-4-5"
    assert captured["prompt_tokens"] == 100
    assert captured["completion_tokens"] == 50


def test_unknown_model_returns_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(**kwargs: Any) -> tuple[float, float]:
        raise ValueError("model not in pricing map")

    monkeypatch.setattr(litellm, "cost_per_token", boom)

    class FakeUsage:
        prompt_tokens = 10
        completion_tokens = 5

    assert compute_cost_usd(usage=FakeUsage(), model="acme/unknown") == 0.0


def test_dict_usage_object_works(monkeypatch: pytest.MonkeyPatch) -> None:
    """Passing a plain dict (no attribute access) must not crash."""
    monkeypatch.setattr(
        litellm,
        "cost_per_token",
        lambda **kwargs: (0.001, 0.002),
    )
    total = compute_cost_usd(
        usage={"prompt_tokens": 100, "completion_tokens": 50},
        model="anthropic/claude-sonnet-4-5",
    )
    assert total == pytest.approx(0.003)


def test_none_usage_treated_as_zero_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    """A missing usage block (None) must not crash; tokens default to 0."""
    captured: dict[str, Any] = {}

    def fake(**kwargs: Any) -> tuple[float, float]:
        captured.update(kwargs)
        return (0.0, 0.0)

    monkeypatch.setattr(litellm, "cost_per_token", fake)
    total = compute_cost_usd(usage=None, model="anthropic/claude-sonnet-4-5")
    assert total == 0.0
    assert captured["prompt_tokens"] == 0
    assert captured["completion_tokens"] == 0


def test_zero_cost_does_not_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    """Some unpriced models return (0, 0) without raising — must yield 0.0."""
    monkeypatch.setattr(
        litellm,
        "cost_per_token",
        lambda **kwargs: (0.0, 0.0),
    )

    class FakeUsage:
        prompt_tokens = 10
        completion_tokens = 5

    assert compute_cost_usd(usage=FakeUsage(), model="acme/free") == 0.0
