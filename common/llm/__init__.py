"""Centralized LLM interface.

All LLM calls in the framework should go through this module. Provides:
- Provider-agnostic API (works with Groq/OpenAI/Anthropic/Gemini/NIM via litellm)
- Centralized retry with exponential backoff on rate limits and transient errors
- Config-driven model and API base resolution
- Tier routing hook (future: cheap vs smart models per use case)

Usage:
    from common.llm import call, call_text

    # Tool-calling loop — returns raw response
    resp = call(messages, tools=tools, tool_choice="auto", max_tokens=1000)
    msg = resp.choices[0].message
    if msg.tool_calls: ...

    # Single prompt — returns content string
    answer = call_text("Classify this: ...", max_tokens=10, temperature=0)
"""

import os
import time
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

DEFAULT_MAX_RETRIES = 4
RETRYABLE_MARKERS = ("429", "500", "502", "503", "504", "rate limit", "timeout")
DEFAULT_FALLBACK_MODEL = "groq/llama-3.3-70b-versatile"
API_KEY_ENV_VARS = (
    "LLM_API_KEY",
    "GROQ_API_KEY",
    "OPENAI_API_KEY",
    "NVIDIA_NIM_API_KEY",
    "ANTHROPIC_API_KEY",
    "GEMINI_API_KEY",
)


def _get_llm_config() -> dict:
    """Read llm: section from config.yaml. Returns {} if missing."""
    try:
        from common.core.config import get_config
        return get_config().get("llm", {}) or {}
    except Exception as e:
        logger.debug(f"config read failed: {e}")
        return {}


def _resolve_api_key(cfg: dict) -> Optional[str]:
    """Try config field, then known env vars in priority order."""
    if cfg.get("api_key"):
        return cfg["api_key"]
    for var in API_KEY_ENV_VARS:
        v = os.getenv(var)
        if v:
            return v
    return None


def _resolve_model(cfg: dict, override: Optional[str], tier: str) -> str:
    """Resolve which model string to use.

    Priority:
      1. explicit override arg
      2. cfg["models"][tier]  (per-tier override, e.g. cfg.models.triage)
      3. cfg["model"]         (single default)
      4. DEFAULT_FALLBACK_MODEL
    """
    if override:
        return override
    tier_map = cfg.get("models") or {}
    if tier_map.get(tier):
        return tier_map[tier]
    return cfg.get("model") or DEFAULT_FALLBACK_MODEL


def _is_retryable(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(m in msg for m in RETRYABLE_MARKERS)


def call(
    messages: list[dict],
    *,
    tools: Optional[list] = None,
    tool_choice: str = "auto",
    max_tokens: int = 1000,
    temperature: float = 0.2,
    model: Optional[str] = None,
    tier: str = "default",
    retry: bool = True,
    extra: Optional[dict] = None,
) -> Any:
    """Make an LLM completion call. Returns the raw litellm response.

    Args:
        messages: OpenAI-format message list.
        tools: Optional function/tool schemas for tool-calling.
        tool_choice: "auto" | "none" | dict — only used when tools is set.
        max_tokens: Output cap.
        temperature: Sampling temperature.
        model: Explicit model override (e.g. "openai/gpt-5.4-mini").
        tier: "default" | "triage" | "strategist" — routes via cfg.models.<tier>.
        retry: If True, retry on retryable errors with exponential backoff.
        extra: Extra kwargs passed through to litellm.completion (e.g. response_format).

    Raises:
        Exception: Re-raises last error after exhausting retries.
    """
    import litellm

    cfg = _get_llm_config()
    resolved_model = _resolve_model(cfg, model, tier)
    api_base = cfg.get("api_base")
    api_key = _resolve_api_key(cfg)

    kwargs: dict[str, Any] = {
        "model": resolved_model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    if api_base:
        kwargs["api_base"] = api_base
    if api_key:
        kwargs["api_key"] = api_key
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = tool_choice
    if extra:
        kwargs.update(extra)

    attempts = DEFAULT_MAX_RETRIES if retry else 1
    last_exc: Optional[Exception] = None
    for attempt in range(attempts):
        try:
            return litellm.completion(**kwargs)
        except Exception as e:
            last_exc = e
            if not retry or attempt == attempts - 1 or not _is_retryable(e):
                raise
            wait = 2 ** attempt * 5  # 5, 10, 20, 40s
            logger.warning(
                f"LLM call failed ({type(e).__name__}: {str(e)[:120]}) — retry in {wait}s"
            )
            time.sleep(wait)
    # Unreachable, but keeps type checker happy
    raise last_exc  # type: ignore[misc]


def call_text(
    prompt_or_messages,
    **kwargs: Any,
) -> str:
    """Convenience wrapper: returns the content string directly.

    Accepts either a single prompt (wrapped as user message) or a messages list.
    """
    if isinstance(prompt_or_messages, str):
        messages = [{"role": "user", "content": prompt_or_messages}]
    else:
        messages = prompt_or_messages
    resp = call(messages, **kwargs)
    content = resp.choices[0].message.content or ""
    return content.strip()


def parse_json_response(content: str) -> Any:
    """Strip markdown code fences and parse JSON. Common cleanup for LLM JSON output."""
    import json
    raw = content.strip()
    if raw.startswith("```"):
        # Strip opening fence + optional language tag
        raw = raw[3:]
        if raw.lower().startswith("json"):
            raw = raw[4:]
        # Strip trailing fence
        if "```" in raw:
            raw = raw.rsplit("```", 1)[0]
    return json.loads(raw.strip())
