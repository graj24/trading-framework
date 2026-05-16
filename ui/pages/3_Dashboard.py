"""Dashboard — portfolio, signals, backtests, news.

Wraps the existing dashboard.py, skipping its set_page_config call
(already set by the multi-page app framework).
"""
import sys
from pathlib import Path
import streamlit as st

ROOT = Path(__file__).parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Read dashboard source and strip the set_page_config call so it doesn't
# conflict with the multi-page app's page config.
src = (ROOT / "dashboard.py").read_text()
src = "\n".join(
    line for line in src.splitlines()
    if "set_page_config" not in line
)
exec(src, {"__name__": "__main__"})  # noqa: S102
