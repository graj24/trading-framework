"""Smoke test for the centralized LLM module refactor.

Runs no network calls — only verifies imports, API surface, and config resolution.
"""
import sys
import os
# Make project root importable when run from any directory
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 1. Import the modules that don't pull in heavy deps locally
import common.strategist.loop  # noqa: F401
import agents.pm_triage  # noqa: F401
print("[OK] light modules import (common.strategist.loop, agents.pm_triage)")

# AST-parse the heavy ones (they import transformers/yfinance which aren't in local venv)
import ast
for f in ["agents/master.py", "pm_1/agents/master.py"]:
    ast.parse(open(f).read())
    print(f"[OK] {f} parses")

# 2. Central module exports
from common.llm import call, call_text, parse_json_response  # noqa: F401
print("[OK] common.llm exports")

# 3. parse_json_response handles common LLM output shapes
assert parse_json_response('{"a": 1}') == {"a": 1}
assert parse_json_response('```json\n{"a": 1}\n```') == {"a": 1}
assert parse_json_response('```\n{"x": 2}\n```') == {"x": 2}
print("[OK] parse_json_response")

# 4. Config + model resolution
from common.llm import _resolve_model, _get_llm_config
cfg = _get_llm_config()
print(f"[OK] config keys: {list(cfg.keys())}")
print(f"     default tier  -> {_resolve_model(cfg, None, 'default')}")
print(f"     strategist    -> {_resolve_model(cfg, None, 'strategist')}")
print(f"     triage        -> {_resolve_model(cfg, None, 'triage')}")
print(f"     explicit ovr  -> {_resolve_model(cfg, 'openai/gpt-5.4-mini', 'default')}")

# 5. Verify no remaining direct litellm.completion calls in hot path
import subprocess
result = subprocess.run(
    ["grep", "-rn", "litellm.completion",
     "common/strategist/", "agents/master.py", "agents/pm_triage.py", "pm_1/agents/master.py"],
    capture_output=True, text=True,
)
if result.stdout.strip():
    print("[FAIL] direct litellm.completion still found:")
    print(result.stdout)
    sys.exit(1)
print("[OK] no direct litellm.completion in hot path")

print("\nAll smoke checks passed.")
