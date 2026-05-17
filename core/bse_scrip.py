# Backward-compat shim — canonical source is common/core/<MODULE>.py
# Uses sys.modules redirect so attribute access (including private names) works.
import sys as _sys
import importlib as _importlib
_real = _importlib.import_module(f"common.core.bse_scrip")
_sys.modules[__name__] = _real
