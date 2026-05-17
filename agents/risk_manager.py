# Backward-compat shim — canonical source is common/agents/<MODULE>.py
import sys as _sys
import importlib as _importlib
_real = _importlib.import_module(f"common.agents.risk_manager")
_sys.modules[__name__] = _real
