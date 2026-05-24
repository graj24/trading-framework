"""Temporal workers — workflows and activities.

`main` is exposed via lazy ``__getattr__`` so that importing the workers
package from a workflow context (which runs inside Temporal's import sandbox)
does not pull in CLI/tty deps like ``tyro`` and ``rich`` — those trigger
``random.getrandbits`` and other sandbox-restricted calls at import time.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from agora.platform.workers.hello import HelloWorkflow, say_hello
from agora.platform.workers.pm_supervisor import (
    HeartbeatInput,
    PMConfig,
    PMSupervisor,
    ProvisionInput,
    ProvisionResult,
    TradingCycleInput,
    TradingCycleOutput,
    get_current_mode,
    heartbeat_journal,
    mark_pm_running,
    mark_pm_stopped,
    provision_pm_workspace,
    trading_cycle_activity,
)

if TYPE_CHECKING:
    from agora.platform.workers.main import main

__all__ = [
    "HeartbeatInput",
    "HelloWorkflow",
    "PMConfig",
    "PMSupervisor",
    "ProvisionInput",
    "ProvisionResult",
    "TradingCycleInput",
    "TradingCycleOutput",
    "get_current_mode",
    "heartbeat_journal",
    "main",
    "mark_pm_running",
    "mark_pm_stopped",
    "provision_pm_workspace",
    "say_hello",
    "trading_cycle_activity",
]


def __getattr__(name: str) -> Any:
    if name == "main":
        from agora.platform.workers.main import main as _main

        return _main
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
