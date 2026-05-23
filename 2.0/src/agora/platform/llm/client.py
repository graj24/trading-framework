"""``AgoraLLM`` — the canonical LLM client for AGORA.

Every LLM call in the platform must go through this class so we get three
things for free:

  1. A litellm call (so we can swap providers without touching agent code).
  2. A Langfuse trace (so the call is observable in Langfuse Cloud).
  3. A ``budget_events`` row (so cost is attributable per-PM).

Failures in (2) and (3) never block (1). The LLM call is the contract; the
Langfuse span and the budget row are bookkeeping. If the bookkeeping path
raises, we log and continue — the caller still gets the model output.

Each instance is bound to ``(agent_id, pm_id)`` for cost attribution.
``pm_id`` may be None for system-level calls (e.g. the Step 1.7 smoke
script) — in that case ``record_budget_event`` is skipped.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import litellm
from langfuse import Langfuse
from loguru import logger

from agora.platform.llm.budget import record_budget_event
from agora.platform.llm.cost import compute_cost_usd
from agora.platform.shared.settings import Settings

CompletionFn = Callable[..., Awaitable[Any]]
CostFn = Callable[..., float]
BudgetRecorder = Callable[..., Awaitable[int | None]]


@dataclass
class LLMCallResult:
    """Return shape from ``AgoraLLM.call``."""

    content: str
    model: str
    tokens_in: int
    tokens_out: int
    cost_usd: float
    langfuse_trace_id: str | None
    raw: Any  # litellm response object — kept around for callers that need more


class AgoraLLM:
    """LLM client with built-in tracing and budget recording.

    See module docstring for the contract.
    """

    def __init__(
        self,
        agent_id: str,
        pm_id: str | None,
        settings: Settings | None = None,
        langfuse: Langfuse | None = None,
        # Allow injection for testing.
        completion_fn: CompletionFn | None = None,
        cost_fn: CostFn | None = None,
        budget_recorder: BudgetRecorder | None = None,
    ) -> None:
        self.agent_id = agent_id
        self.pm_id = pm_id
        self.settings = settings or Settings()
        self._langfuse_override = langfuse
        self._completion_fn: CompletionFn = completion_fn or litellm.acompletion
        self._cost_fn: CostFn = cost_fn or compute_cost_usd
        self._budget_recorder: BudgetRecorder = budget_recorder or record_budget_event
        # Per-instance Langfuse cache. Built lazily on first .call() — see
        # _maybe_langfuse(). Once set, reused for the lifetime of the
        # instance. Caching None on construction failure is intentional: do
        # not retry the SDK per call.
        self._langfuse_client_cache: Langfuse | None = None
        self._langfuse_client_built: bool = False

    # ------------------------------------------------------------------ public

    async def call(
        self,
        model: str,
        messages: list[dict[str, str]],
        task_id: str | None = None,
        **kwargs: Any,
    ) -> LLMCallResult:
        """Issue one LLM call, trace it, record cost, return the result."""
        trace_id = str(uuid4())
        langfuse_client = self._maybe_langfuse()
        trace = self._open_trace(
            langfuse_client,
            trace_id=trace_id,
            model=model,
            messages=messages,
            task_id=task_id,
        )

        # The actual LLM call. Failures here propagate — the caller wants to
        # know if Anthropic is down.
        response = await self._completion_fn(model=model, messages=messages, **kwargs)

        content, tokens_in, tokens_out, usage = _extract_response(response)
        cost_usd = self._safe_compute_cost(usage, model)

        if self.pm_id is not None:
            await self._safe_record_budget(
                kind="llm_call",
                cost_usd=cost_usd,
                metadata={
                    "agent_id": self.agent_id,
                    "model": model,
                    "tokens_in": tokens_in,
                    "tokens_out": tokens_out,
                    "task_id": task_id,
                    "langfuse_trace_id": trace_id if trace is not None else None,
                },
            )
        else:
            logger.debug(
                "AgoraLLM.call: pm_id is None; skipping budget event "
                "(agent_id={agent_id} model={model} cost_usd={cost})",
                agent_id=self.agent_id,
                model=model,
                cost=cost_usd,
            )

        self._close_trace(
            langfuse_client,
            trace,
            input_messages=messages,
            output_content=content,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=cost_usd,
        )

        return LLMCallResult(
            content=content,
            model=model,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=cost_usd,
            langfuse_trace_id=trace_id if trace is not None else None,
            raw=response,
        )

    # ----------------------------------------------------------------- helpers

    def _maybe_langfuse(self) -> Langfuse | None:
        """Return a Langfuse client if keys are configured, else None.

        Memoized per-instance: the SDK is built once on first call, then
        reused. Construction failures are cached as ``None`` — we do not
        retry the SDK per call, because the failure modes (bad keys,
        version skew) are persistent.

        Langfuse failures must never block the LLM call. If construction
        raises, we log a warning and cache ``None``; callers will see
        ``langfuse_trace_id=None``.
        """
        if self._langfuse_override is not None:
            return self._langfuse_override
        if self._langfuse_client_built:
            return self._langfuse_client_cache
        self._langfuse_client_built = True
        if not self.settings.langfuse_public_key or not self.settings.langfuse_secret_key:
            self._langfuse_client_cache = None
            return None
        try:
            self._langfuse_client_cache = Langfuse(
                public_key=self.settings.langfuse_public_key,
                secret_key=self.settings.langfuse_secret_key,
                host=self.settings.langfuse_host,
            )
        except Exception as e:
            logger.warning(
                "Langfuse construction failed; continuing without tracing: {err}",
                err=f"{type(e).__name__}: {e}",
            )
            self._langfuse_client_cache = None
        return self._langfuse_client_cache

    def _open_trace(
        self,
        client: Langfuse | None,
        *,
        trace_id: str,
        model: str,
        messages: list[dict[str, str]],
        task_id: str | None,
    ) -> Any:
        if client is None:
            return None
        try:
            return client.trace(
                id=trace_id,
                name=f"{self.agent_id}.llm_call",
                input=messages,
                metadata={
                    "agent_id": self.agent_id,
                    "pm_id": self.pm_id,
                    "task_id": task_id,
                    "model": model,
                },
            )
        except Exception as e:
            logger.warning(
                "Langfuse trace() raised; continuing without tracing: {err}",
                err=f"{type(e).__name__}: {e}",
            )
            return None

    def _close_trace(
        self,
        client: Langfuse | None,
        trace: Any,
        *,
        input_messages: list[dict[str, str]],
        output_content: str,
        tokens_in: int,
        tokens_out: int,
        cost_usd: float,
    ) -> None:
        if client is None or trace is None:
            return
        try:
            trace.update(
                output=output_content,
                metadata={
                    "tokens_in": tokens_in,
                    "tokens_out": tokens_out,
                    "cost_usd": cost_usd,
                },
            )
            client.flush()
        except Exception as e:
            logger.warning(
                "Langfuse trace.update/flush raised; trace may be incomplete: {err}",
                err=f"{type(e).__name__}: {e}",
            )

    def _safe_compute_cost(self, usage: Any, model: str) -> float:
        try:
            return float(self._cost_fn(usage=usage, model=model))
        except Exception as e:
            logger.error(
                "cost_fn raised: {err}; recording 0.0",
                err=f"{type(e).__name__}: {e}",
            )
            return 0.0

    async def _safe_record_budget(
        self,
        *,
        kind: str,
        cost_usd: float,
        metadata: dict[str, Any],
    ) -> None:
        try:
            await self._budget_recorder(
                pm_id=self.pm_id,
                kind=kind,
                amount_usd=cost_usd,
                metadata=metadata,
            )
        except Exception as e:
            logger.error(
                "budget recorder raised: {err}; call succeeded but row not written",
                err=f"{type(e).__name__}: {e}",
            )


# --------------------------------------------------------------------- parsing


def _extract_response(response: Any) -> tuple[str, int, int, Any]:
    """Pull (content, tokens_in, tokens_out, usage) off a litellm response.

    Defensive: tests pass plain dicts, real litellm returns ModelResponse.
    """

    def _get(obj: Any, key: str, default: Any = None) -> Any:
        if obj is None:
            return default
        value: Any = getattr(obj, key, None)
        if value is None and isinstance(obj, dict):
            value = obj.get(key, default)
        return default if value is None else value

    choices = _get(response, "choices", []) or []
    content = ""
    if choices:
        first = choices[0]
        message = _get(first, "message")
        content = _get(message, "content", "") or ""

    usage = _get(response, "usage")
    tokens_in = int(_get(usage, "prompt_tokens", 0) or 0)
    tokens_out = int(_get(usage, "completion_tokens", 0) or 0)
    return content, tokens_in, tokens_out, usage


__all__ = ["AgoraLLM", "LLMCallResult"]
