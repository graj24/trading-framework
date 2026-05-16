"""Ripple sentiment subsystem configuration.

Reads:
* ``DEFAULT_MAX_TWEETS`` — fan-out cap for the collector.
* ``OUTPUT_DIR``         — where ``StockSentimentPipeline.export_to_json``
  writes its results. Defaults to ``<repo_root>/output``.

Both ``Config.X`` and module-level ``X`` accessors are exposed so existing
callers and the new ``ripple.pipeline`` import path keep working.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# Repo root is the parent of the `ripple/` package.
_REPO_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_OUTPUT_DIR = _REPO_ROOT / "output"

DEFAULT_MAX_TWEETS: int = int(os.getenv("DEFAULT_MAX_TWEETS", 10))
OUTPUT_DIR: str = os.getenv("OUTPUT_DIR", str(_DEFAULT_OUTPUT_DIR))


class Config:
    """Back-compat shim for callers that referenced ``Config.X``."""

    DEFAULT_MAX_TWEETS = DEFAULT_MAX_TWEETS
    OUTPUT_DIR = OUTPUT_DIR
