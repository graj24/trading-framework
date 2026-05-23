"""Unit tests for ``AgoraLLM``.

All external dependencies are injected so no network or DB is required:

  * ``completion_fn``   — fake litellm response (dict-shaped is fine).
  * ``cost_fn``         — fixed dollar amount (or raising stub).
  * ``budget_recorder`` — async stub that records calls.
  * ``langfuse``        — stub Langfuse client (or raising stub).

These cover the wrapper's three contracts:
  1. The LLM call always returns content.
  2. Bookkeeping (Langfuse + budget) is best-effort and never blocks (1).
  3. ``pm_id=None`` skips budget recording entirely.
"""

from __future__ import annotations

from typing import Any

import pytest

from agora.platform.llm.client import AgoraLLM
from agora.platform.shared.settings import Settings


def _settings() -> Settings:
    # Disable .env so test runs are deterministic regardless of host machine.
    return Settings(_env_file=None)


def _fake_response(
    content: str = "hi", prompt_tokens: int = 7, completion_tokens: int = 3
) -> dict[str, Any]:
    """A litellm-shaped response. We use plain dicts to exercise the
    fallback path in ``_extract_response`` (object-attribute access first,
    dict ``.get`` second)."""
    return {
        "choices": [{"message": {"content": content}}],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }


class _RecordingBudget:
    """Async stub: records calls instead of writing to Postgres."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, **kwargs: Any) -> int | None:
        self.calls.append(kwargs)
        return 1


async def test_call_records_cost_and_returns_content() -> None:
    budget = _RecordingBudget()

    async def fake_completion(**kwargs: Any) -> dict[str, Any]:
        assert kwargs["model"] == "anthropic/claude-sonnet-4-5"
        return _fake_response(content="hello, AGORA", prompt_tokens=10, completion_tokens=4)

    llm = AgoraLLM(
        agent_id="smoke",
        pm_id="pm1",
        settings=_settings(),
        completion_fn=fake_completion,
        cost_fn=lambda usage, model: 0.0042,
        budget_recorder=budget,
    )

    result = await llm.call(
        model="anthropic/claude-sonnet-4-5",
        messages=[{"role": "user", "content": "hi"}],
        task_id="task-7",
    )

    assert result.content == "hello, AGORA"
    assert result.tokens_in == 10
    assert result.tokens_out == 4
    assert result.cost_usd == 0.0042
    # Langfuse is unconfigured in the default Settings — no trace id.
    assert result.langfuse_trace_id is None

    assert len(budget.calls) == 1
    call = budget.calls[0]
    assert call["pm_id"] == "pm1"
    assert call["kind"] == "llm_call"
    assert call["amount_usd"] == 0.0042
    md = call["metadata"]
    assert md["model"] == "anthropic/claude-sonnet-4-5"
    assert md["tokens_in"] == 10
    assert md["tokens_out"] == 4
    assert md["task_id"] == "task-7"
    assert md["agent_id"] == "smoke"


async def test_call_with_no_pm_id_skips_budget() -> None:
    """When pm_id is None, the recorder is NOT called (per spec — system-level
    calls are not attributed to any PM)."""
    budget = _RecordingBudget()

    async def fake_completion(**kwargs: Any) -> dict[str, Any]:
        return _fake_response()

    llm = AgoraLLM(
        agent_id="smoke",
        pm_id=None,
        settings=_settings(),
        completion_fn=fake_completion,
        cost_fn=lambda usage, model: 0.001,
        budget_recorder=budget,
    )

    result = await llm.call(
        model="anthropic/claude-sonnet-4-5",
        messages=[{"role": "user", "content": "hi"}],
    )
    assert result.content == "hi"
    assert budget.calls == []


async def test_call_swallows_langfuse_errors() -> None:
    """If Langfuse.trace() raises, the call still succeeds."""

    class BoomLangfuse:
        def trace(self, **kwargs: Any) -> Any:
            raise RuntimeError("langfuse exploded")

        def flush(self) -> None:  # pragma: no cover — never reached
            raise RuntimeError("flush exploded")

    async def fake_completion(**kwargs: Any) -> dict[str, Any]:
        return _fake_response(content="ok")

    llm = AgoraLLM(
        agent_id="smoke",
        pm_id="pm1",
        settings=_settings(),
        langfuse=BoomLangfuse(),
        completion_fn=fake_completion,
        cost_fn=lambda usage, model: 0.001,
        budget_recorder=_RecordingBudget(),
    )

    result = await llm.call(
        model="anthropic/claude-sonnet-4-5",
        messages=[{"role": "user", "content": "hi"}],
    )
    assert result.content == "ok"
    assert result.langfuse_trace_id is None


async def test_call_swallows_budget_errors() -> None:
    """If the budget recorder raises, the call still succeeds."""

    async def boom(**kwargs: Any) -> int:
        raise RuntimeError("DB unavailable")

    async def fake_completion(**kwargs: Any) -> dict[str, Any]:
        return _fake_response(content="still ok")

    llm = AgoraLLM(
        agent_id="smoke",
        pm_id="pm1",
        settings=_settings(),
        completion_fn=fake_completion,
        cost_fn=lambda usage, model: 0.001,
        budget_recorder=boom,
    )

    result = await llm.call(
        model="anthropic/claude-sonnet-4-5",
        messages=[{"role": "user", "content": "hi"}],
    )
    assert result.content == "still ok"
    assert result.cost_usd == 0.001  # cost still computed; recording is what failed


async def test_call_swallows_cost_errors() -> None:
    """If the cost fn raises, cost is recorded as 0.0 and call still succeeds."""

    def boom_cost(usage: Any, model: str) -> float:
        raise RuntimeError("pricing map ate the model")

    async def fake_completion(**kwargs: Any) -> dict[str, Any]:
        return _fake_response(content="still ok")

    budget = _RecordingBudget()
    llm = AgoraLLM(
        agent_id="smoke",
        pm_id="pm1",
        settings=_settings(),
        completion_fn=fake_completion,
        cost_fn=boom_cost,
        budget_recorder=budget,
    )

    result = await llm.call(
        model="anthropic/claude-sonnet-4-5",
        messages=[{"role": "user", "content": "hi"}],
    )
    assert result.content == "still ok"
    assert result.cost_usd == 0.0
    assert budget.calls[0]["amount_usd"] == 0.0


async def test_call_includes_trace_id_when_langfuse_configured() -> None:
    """When a Langfuse client is provided, the wrapper opens a trace and
    surfaces its id on the result."""

    captured: dict[str, Any] = {}

    class StubTrace:
        def update(self, **kwargs: Any) -> None:
            captured["update"] = kwargs

    class StubLangfuse:
        def trace(self, **kwargs: Any) -> StubTrace:
            captured["trace"] = kwargs
            return StubTrace()

        def flush(self) -> None:
            captured["flushed"] = True

    async def fake_completion(**kwargs: Any) -> dict[str, Any]:
        return _fake_response(content="hi", prompt_tokens=2, completion_tokens=1)

    llm = AgoraLLM(
        agent_id="smoke",
        pm_id="pm1",
        settings=_settings(),
        langfuse=StubLangfuse(),
        completion_fn=fake_completion,
        cost_fn=lambda usage, model: 0.0001,
        budget_recorder=_RecordingBudget(),
    )

    result = await llm.call(
        model="anthropic/claude-sonnet-4-5",
        messages=[{"role": "user", "content": "hi"}],
        task_id="t-1",
    )
    assert result.langfuse_trace_id is not None
    # Trace was opened with our metadata.
    assert captured["trace"]["name"] == "smoke.llm_call"
    assert captured["trace"]["metadata"]["agent_id"] == "smoke"
    assert captured["trace"]["metadata"]["pm_id"] == "pm1"
    assert captured["trace"]["metadata"]["task_id"] == "t-1"
    # Trace was updated with output + token counts + cost, then flushed.
    assert captured["update"]["output"] == "hi"
    assert captured["update"]["metadata"]["tokens_in"] == 2
    assert captured["update"]["metadata"]["tokens_out"] == 1
    assert captured["update"]["metadata"]["cost_usd"] == 0.0001
    assert captured.get("flushed") is True


@pytest.mark.parametrize("malformed", [{}, {"choices": []}, {"choices": [{}]}])
async def test_call_handles_malformed_response_gracefully(malformed: dict[str, Any]) -> None:
    """Empty / malformed responses do not crash the wrapper."""

    async def fake_completion(**kwargs: Any) -> dict[str, Any]:
        return malformed

    llm = AgoraLLM(
        agent_id="smoke",
        pm_id=None,
        settings=_settings(),
        completion_fn=fake_completion,
        cost_fn=lambda usage, model: 0.0,
        budget_recorder=_RecordingBudget(),
    )
    result = await llm.call(
        model="anthropic/claude-sonnet-4-5",
        messages=[{"role": "user", "content": "hi"}],
    )
    assert result.content == ""
    assert result.tokens_in == 0
    assert result.tokens_out == 0
