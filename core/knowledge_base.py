"""Per-stock knowledge base management — read/write stock-specific data."""
import json
import os
from pathlib import Path


STOCKS_DIR = Path(__file__).parent.parent / "stocks"

KB_FILES = [
    "price_history.parquet",
    "fundamentals.json",
    "news_history.json",
    "earnings_history.json",
    "corporate_actions.json",
    "bulk_deals.json",
    "sector_correlation.json",
    "patterns.json",
    "event_reactions.json",
    "signal_weights.json",
]


def kb_path(symbol: str) -> Path:
    return STOCKS_DIR / symbol.upper()


def init_kb(symbol: str) -> None:
    """Create knowledge base directory and empty JSON files for a stock."""
    path = kb_path(symbol)
    path.mkdir(parents=True, exist_ok=True)
    for fname in KB_FILES:
        fpath = path / fname
        if not fpath.exists() and fname.endswith(".json"):
            fpath.write_text("{}")


def read_kb(symbol: str, key: str) -> dict:
    fpath = kb_path(symbol) / key
    if not fpath.exists():
        return {}
    return json.loads(fpath.read_text())


def write_kb(symbol: str, key: str, data: dict) -> None:
    fpath = kb_path(symbol) / key
    fpath.parent.mkdir(parents=True, exist_ok=True)
    fpath.write_text(json.dumps(data, indent=2, default=str))
