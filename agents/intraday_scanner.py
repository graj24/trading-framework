# Backward-compat shim — canonical source is pm_1/agents/intraday_scanner.py
# PM1 owns this agent. Other PMs should create their own in pm_<id>/agents/.
import sys as _sys, importlib as _il, importlib.util as _ilu
_spec = _ilu.spec_from_file_location(
    "pm_1.agents.intraday_scanner",
    __file__.replace("/agents/intraday_scanner.py", "/pm_1/agents/intraday_scanner.py"),
)
_mod = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
_sys.modules[__name__] = _mod
