"""HTTP retry / backoff helpers.

Most of this codebase makes bare ``requests.get`` / ``yfinance`` calls
without retry logic. yfinance in particular fails silently with empty
DataFrames when rate-limited, so an undetected network blip looks like
"no signal".

This module provides:

* ``retry()`` — decorator with exponential backoff + jitter.
* ``with_retry(callable, ...)`` — one-shot retry wrapper.

Intentionally tiny — no `tenacity` dep. If the project later grows to
need fancy retry policies (per-status-code, per-exception), upgrade then.
"""
from __future__ import annotations

import functools
import logging
import random
import time
from typing import Any, Callable, Iterable

logger = logging.getLogger("trading.retry")

# Default exception set covers requests / urllib / generic OS errors.
DEFAULT_RETRYABLE: tuple[type[BaseException], ...] = (
    OSError,
    ConnectionError,
    TimeoutError,
)


def _sleep_with_jitter(base: float, attempt: int, max_sleep: float) -> None:
    raw = base * (2 ** attempt)
    capped = min(raw, max_sleep)
    # ±25% jitter — avoids thundering herds against rate-limited APIs.
    jitter = capped * (0.75 + 0.5 * random.random())
    time.sleep(jitter)


def with_retry(
    fn: Callable[..., Any],
    *args: Any,
    attempts: int = 3,
    base_sleep: float = 0.5,
    max_sleep: float = 8.0,
    retryable: Iterable[type[BaseException]] = DEFAULT_RETRYABLE,
    **kwargs: Any,
) -> Any:
    """Call ``fn(*args, **kwargs)`` with up to ``attempts`` retries on
    listed exception types.

    Logs at WARNING on each retry, ERROR on final failure. Re-raises the
    last exception when retries are exhausted.
    """
    retryable = tuple(retryable)
    last_exc: BaseException | None = None
    for attempt in range(attempts):
        try:
            return fn(*args, **kwargs)
        except retryable as exc:
            last_exc = exc
            if attempt + 1 >= attempts:
                break
            logger.warning(
                "retry | fn=%s attempt=%d/%d error=%s",
                getattr(fn, "__name__", str(fn)), attempt + 1, attempts, exc,
            )
            _sleep_with_jitter(base_sleep, attempt, max_sleep)
    logger.error(
        "retry | fn=%s exhausted %d attempts: %s",
        getattr(fn, "__name__", str(fn)), attempts, last_exc,
    )
    raise last_exc  # type: ignore[misc]


def retry(
    attempts: int = 3,
    base_sleep: float = 0.5,
    max_sleep: float = 8.0,
    retryable: Iterable[type[BaseException]] = DEFAULT_RETRYABLE,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator form of :func:`with_retry`.

    Example::

        @retry(attempts=5)
        def fetch_quote(symbol): ...
    """

    def deco(fn: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            return with_retry(
                fn, *args, attempts=attempts,
                base_sleep=base_sleep, max_sleep=max_sleep,
                retryable=retryable, **kwargs,
            )

        return wrapper

    return deco
