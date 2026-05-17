"""Sandboxed tools for PM agents.

Each tool is a plain Python function. The tool registry exposes them as
OpenAI-compatible function schemas for litellm tool_calls.

Safety rules enforced here (not by the LLM):
- read_file: only pm_<id>/, stocks/, data/, common/ (no .env, no secrets)
- write_file: only pm_<id>/ (PMs cannot touch each other's workspaces or core/)
- shell: 30s timeout, no network (offline), no sudo
- sql_query: read-only SELECT only
"""
from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import subprocess
import textwrap
import time
from pathlib import Path
from typing import Any

import requests

logger = logging.getLogger(__name__)

_APP_ROOT = Path(__file__).resolve().parent.parent.parent


# ── Safety helpers ────────────────────────────────────────────────────────────

def _safe_read_path(pm_id: str, path: str) -> Path:
    """Resolve path and assert it's in an allowed read zone."""
    p = (_APP_ROOT / path).resolve()
    allowed = [
        _APP_ROOT / f"pm_{pm_id}",
        _APP_ROOT / "stocks",
        _APP_ROOT / "data",
        _APP_ROOT / "common",
        _APP_ROOT / "agents",
        _APP_ROOT / "core",
        _APP_ROOT / "config.yaml",
        _APP_ROOT / "requirements.txt",
    ]
    # Also allow reading other PMs' workspaces (strategies, journal — for competitive intel)
    # but never their .env or secrets
    for d in _APP_ROOT.glob("pm_*/"):
        allowed.append(d)
    if not any(str(p).startswith(str(a)) for a in allowed):
        raise PermissionError(f"read_file: path '{path}' is outside allowed zones")
    # Never expose secrets
    if p.name in (".env", ".env.example") or ".pem" in p.name:
        raise PermissionError(f"read_file: '{path}' is a secrets file")
    return p


def _safe_write_path(pm_id: str, path: str) -> Path:
    """Resolve path and assert it's inside this PM's workspace."""
    p = (_APP_ROOT / path).resolve()
    allowed = _APP_ROOT / f"pm_{pm_id}"
    if not str(p).startswith(str(allowed)):
        raise PermissionError(f"write_file: path '{path}' is outside pm_{pm_id}/ workspace")
    return p


# ── Tool implementations ──────────────────────────────────────────────────────

def read_file(pm_id: str, path: str, start_line: int = 1, end_line: int = 200) -> str:
    """Read lines [start_line, end_line] from a file (1-indexed)."""
    p = _safe_read_path(pm_id, path)
    if not p.exists():
        return f"ERROR: file not found: {path}"
    if p.is_dir():
        entries = sorted(p.iterdir())
        return "\n".join(e.name + ("/" if e.is_dir() else "") for e in entries[:100])
    lines = p.read_text(errors="replace").splitlines()
    sl, el = max(0, start_line - 1), min(len(lines), end_line)
    chunk = lines[sl:el]
    header = f"# {path} (lines {sl+1}–{sl+len(chunk)} of {len(lines)})\n"
    return header + "\n".join(chunk)


def write_file(pm_id: str, path: str, content: str) -> str:
    """Write content to a file inside pm_<id>/. Creates parent dirs."""
    p = _safe_write_path(pm_id, path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    return f"OK: wrote {len(content)} chars to {path}"


def append_file(pm_id: str, path: str, content: str) -> str:
    """Append content to a file inside pm_<id>/."""
    p = _safe_write_path(pm_id, path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a") as f:
        f.write(content)
    return f"OK: appended {len(content)} chars to {path}"


def run_shell(pm_id: str, cmd: str) -> str:
    """Run a shell command in /app with 30s timeout. No network, no sudo."""
    # Block dangerous patterns
    blocked = ["sudo", "rm -rf", "curl ", "wget ", "pip install", "> /dev/", "mkfs",
               "dd if=", "chmod 777", "chown root", ":(){", "fork bomb"]
    for b in blocked:
        if b in cmd:
            return f"ERROR: blocked command pattern '{b}'"
    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True,
            timeout=30, cwd=str(_APP_ROOT),
            env={**os.environ, "PYTHONPATH": str(_APP_ROOT)},
        )
        out = (result.stdout or "")[-3000:]  # cap output
        err = (result.stderr or "")[-1000:]
        if result.returncode != 0:
            return f"EXIT {result.returncode}\nSTDOUT:\n{out}\nSTDERR:\n{err}"
        return out or "(no output)"
    except subprocess.TimeoutExpired:
        return "ERROR: command timed out after 30s"
    except Exception as e:
        return f"ERROR: {e}"


def run_pytest(pm_id: str, test_path: str = "") -> str:
    """Run pytest on pm_<id>/tests/ or a specific test file."""
    if test_path:
        p = _safe_read_path(pm_id, test_path)
        target = str(p)
    else:
        target = str(_APP_ROOT / f"pm_{pm_id}" / "tests")
        if not Path(target).exists():
            return f"OK: no tests directory at pm_{pm_id}/tests/ — nothing to run"
    try:
        result = subprocess.run(
            [str(_APP_ROOT / ".venv/bin/python"), "-m", "pytest", target,
             "--tb=short", "-q", "--no-header", "--timeout=60"],
            capture_output=True, text=True, timeout=120, cwd=str(_APP_ROOT),
            env={**os.environ, "PYTHONPATH": str(_APP_ROOT)},
        )
        out = (result.stdout + result.stderr)[-3000:]
        return out
    except subprocess.TimeoutExpired:
        return "ERROR: pytest timed out after 120s"
    except Exception as e:
        return f"ERROR: {e}"


def web_fetch(pm_id: str, url: str, search_terms: str = "") -> str:
    """Fetch a URL and return text content (max 4000 chars)."""
    # Whitelist: financial data sources only
    allowed_domains = [
        "nseindia.com", "bseindia.com", "moneycontrol.com", "screener.in",
        "tickertape.in", "trendlyne.com", "nsdl.co.in", "sebi.gov.in",
        "rbi.org.in", "finance.yahoo.com", "economictimes.indiatimes.com",
        "livemint.com", "businessstandard.com", "github.com", "pypi.org",
        "docs.python.org", "pandas.pydata.org", "numpy.org",
    ]
    from urllib.parse import urlparse
    domain = urlparse(url).netloc.lstrip("www.")
    if not any(domain.endswith(d) for d in allowed_domains):
        return f"ERROR: domain '{domain}' not in allowed list. Allowed: {allowed_domains}"
    try:
        r = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        # Strip HTML tags
        text = re.sub(r"<[^>]+>", " ", r.text)
        text = re.sub(r"\s+", " ", text).strip()
        if search_terms:
            # Return context around search terms
            terms = search_terms.lower().split()
            lines = text.split(". ")
            relevant = [l for l in lines if any(t in l.lower() for t in terms)]
            text = ". ".join(relevant[:30])
        return text[:4000]
    except Exception as e:
        return f"ERROR: {e}"


def web_search(pm_id: str, query: str) -> str:
    """Search DuckDuckGo and return top results (no API key needed)."""
    try:
        r = requests.get(
            "https://api.duckduckgo.com/",
            params={"q": query, "format": "json", "no_html": "1", "skip_disambig": "1"},
            timeout=10,
        )
        data = r.json()
        results = []
        if data.get("AbstractText"):
            results.append(f"Summary: {data['AbstractText'][:500]}")
        for item in data.get("RelatedTopics", [])[:5]:
            if isinstance(item, dict) and item.get("Text"):
                results.append(f"- {item['Text'][:200]}")
        return "\n".join(results) if results else "No results found."
    except Exception as e:
        return f"ERROR: {e}"


def sql_query(pm_id: str, sql: str) -> str:
    """Run a read-only SQL query on paper_trades.db or events.db."""
    sql_stripped = sql.strip().upper()
    if not sql_stripped.startswith("SELECT"):
        return "ERROR: only SELECT queries allowed"
    if any(kw in sql_stripped for kw in ("DROP", "DELETE", "UPDATE", "INSERT", "CREATE", "ALTER")):
        return "ERROR: only SELECT queries allowed"
    try:
        db = _APP_ROOT / "paper_trades.db"
        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(sql).fetchall()[:50]
        conn.close()
        if not rows:
            return "No rows returned."
        cols = rows[0].keys()
        lines = [" | ".join(cols)]
        lines += [" | ".join(str(r[c]) for c in cols) for r in rows]
        return "\n".join(lines)
    except Exception as e:
        return f"ERROR: {e}"


def memory_store(pm_id: str, key: str, content: str) -> str:
    """Store a research finding in PM's persistent memory."""
    from common.memory import get_store
    get_store(pm_id).put(key, content)
    return f"OK: stored '{key}' in memory"


def memory_search(pm_id: str, query: str, top_k: int = 5) -> str:
    """Search PM's persistent memory for relevant past findings."""
    from common.memory import get_store
    results = get_store(pm_id).search(query, top_k=top_k)
    if not results:
        return "No relevant memories found."
    return "\n\n".join(f"[{r['key']}] {r['content'][:300]}" for r in results)


# ── Tool registry (OpenAI function schema) ────────────────────────────────────

def get_tool_schemas() -> list[dict]:
    return [
        {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "Read a file or list a directory. Allowed paths: pm_<id>/, stocks/, data/, common/, agents/, core/, config.yaml",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Relative path from /app root"},
                        "start_line": {"type": "integer", "description": "First line to read (1-indexed, default 1)"},
                        "end_line": {"type": "integer", "description": "Last line to read (default 200)"},
                    },
                    "required": ["path"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "write_file",
                "description": "Write a file inside your own pm_<id>/ workspace. Creates parent dirs. Use this to create new agents, strategies, tests.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Path inside pm_<id>/ (e.g. pm_2/agents/fii_scraper.py)"},
                        "content": {"type": "string", "description": "Full file content"},
                    },
                    "required": ["path", "content"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "run_shell",
                "description": "Run a shell command in /app (30s timeout). Use for: running Python scripts, checking file sizes, listing processes. No network, no sudo.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "cmd": {"type": "string", "description": "Shell command to run"},
                    },
                    "required": ["cmd"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "run_pytest",
                "description": "Run pytest on your pm_<id>/tests/ directory or a specific test file. Always run tests after writing new code.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "test_path": {"type": "string", "description": "Optional: specific test file path. Defaults to pm_<id>/tests/"},
                    },
                    "required": [],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "web_fetch",
                "description": "Fetch a URL and return its text content. Allowed domains: NSE, BSE, Screener, Moneycontrol, NSDL, SEBI, RBI, Yahoo Finance, ET, Mint, BS, GitHub, PyPI.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string"},
                        "search_terms": {"type": "string", "description": "Optional: keywords to filter relevant sections"},
                    },
                    "required": ["url"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "web_search",
                "description": "Search DuckDuckGo for financial data, NSE stocks, market news. Returns top results.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                    },
                    "required": ["query"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "sql_query",
                "description": "Run a read-only SELECT query on paper_trades.db. Tables: trades (id, symbol, pm_id, entry_price, exit_price, pnl_inr, pnl_pct, outcome, entry_date, exit_date, tag).",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "sql": {"type": "string", "description": "SELECT query only"},
                    },
                    "required": ["sql"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "memory_store",
                "description": "Store a research finding or insight in your persistent memory for future cycles.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "key": {"type": "string", "description": "Short label for this memory (e.g. 'fii_flow_signal_2026-05')"},
                        "content": {"type": "string", "description": "The finding or insight to remember"},
                    },
                    "required": ["key", "content"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "memory_search",
                "description": "Search your past research findings and insights.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "top_k": {"type": "integer", "description": "Number of results (default 5)"},
                    },
                    "required": ["query"],
                },
            },
        },
    ]


def dispatch(pm_id: str, tool_name: str, args: dict) -> str:
    """Dispatch a tool call. Returns string result."""
    fns = {
        "read_file": read_file,
        "write_file": write_file,
        "append_file": append_file,
        "run_shell": run_shell,
        "run_pytest": run_pytest,
        "web_fetch": web_fetch,
        "web_search": web_search,
        "sql_query": sql_query,
        "memory_store": memory_store,
        "memory_search": memory_search,
    }
    fn = fns.get(tool_name)
    if not fn:
        return f"ERROR: unknown tool '{tool_name}'"
    try:
        return fn(pm_id=pm_id, **args)
    except PermissionError as e:
        return f"PERMISSION DENIED: {e}"
    except Exception as e:
        logger.warning(f"Tool {tool_name} failed: {e}")
        return f"ERROR: {e}"
