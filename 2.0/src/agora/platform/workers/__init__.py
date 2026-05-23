"""Temporal workers — workflows and activities.

`main` is exposed via lazy ``__getattr__`` so that importing the workers
package from a workflow context (which runs inside Temporal's import sandbox)
does not pull in CLI/tty deps like ``tyro`` and ``rich`` — those trigger
``random.getrandbits`` and other sandbox-restricted calls at import time.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from agora.platform.workers.hello import HelloWorkflow, say_hello

if TYPE_CHECKING:
    from agora.platform.workers.main import main

__all__ = ["HelloWorkflow", "main", "say_hello"]


def __getattr__(name: str) -> Any:
    if name == "main":
        from agora.platform.workers.main import main as _main

        return _main
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
