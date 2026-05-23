"""USD cost computation for an LLM call.

We do not call ``litellm.completion_cost()`` because that helper expects a
fully-formed ModelResponse object (with timing, choices, etc). Inside the
wrapper we have a raw response and a ``usage`` block — the lightest path is
``litellm.cost_per_token`` which returns ``(prompt_cost, completion_cost)``
in USD given just (model, prompt_tokens, completion_tokens).

If litellm's pricing map does not know the model (new releases, custom
provider names), it raises or returns zeros. We catch and log a warning, then
return 0.0 so cost-recording never blocks the call. The caller (AgoraLLM)
treats 0.0 cost as "unpriced, recorded" rather than "free".
"""

from __future__ import annotations

from typing import Any

import litellm
from loguru import logger


def _read_token_count(usage: Any, key: str) -> int:
    """Pull ``key`` off a litellm Usage-shaped object or dict, defaulting to 0."""
    # Attribute access first (litellm's Pydantic-shaped Usage), then __getitem__,
    # then .get(...) on dicts. Anything else → 0.
    value: Any = getattr(usage, key, None)
    if value is None and isinstance(usage, dict):
        value = usage.get(key, 0)
    if value is None:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def compute_cost_usd(usage: Any, model: str) -> float:
    """Compute the USD cost of one LLM call.

    Returns 0.0 (logged as a warning) if litellm cannot price the model.
    """
    prompt_tokens = _read_token_count(usage, "prompt_tokens")
    completion_tokens = _read_token_count(usage, "completion_tokens")

    try:
        prompt_cost, completion_cost = litellm.cost_per_token(
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )
    except Exception as e:
        logger.warning(
            "litellm.cost_per_token failed for model={model}: {err}; recording 0.0",
            model=model,
            err=f"{type(e).__name__}: {e}",
        )
        return 0.0

    total = float(prompt_cost or 0.0) + float(completion_cost or 0.0)
    if total == 0.0:
        logger.warning(
            "litellm priced model={model} at $0.00 (likely unknown); "
            "tokens_in={tokens_in} tokens_out={tokens_out}",
            model=model,
            tokens_in=prompt_tokens,
            tokens_out=completion_tokens,
        )
    return total


__all__ = ["compute_cost_usd"]
