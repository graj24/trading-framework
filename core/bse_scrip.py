"""BSE scrip-code ↔ symbol lookup.

Data source: Shoonya/Finvasia BSE symbol master (publicly available at
https://api.shoonya.com/BSE_symbols.txt.zip), filtered to equity segments.

Bundled snapshot: ``data/bse_scrip_master.csv`` (scrip_code, symbol).

Auto-refresh: call ``refresh()`` to download a fresh copy from Shoonya.
The bundled CSV is used as a fallback when the network is unavailable.
"""
from __future__ import annotations

import csv
import io
import logging
import zipfile
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "bse_scrip_master.csv"
_SHOONYA_URL = "https://api.shoonya.com/BSE_symbols.txt.zip"

# Equity instrument groups in the Shoonya master
_EQUITY_GROUPS = {
    "A", "B", "T", "S", "X", "XT", "XD", "Z", "M", "MT",
    "ST", "P", "R", "IF", "IT", "IV",
}

# Module-level cache: populated lazily on first lookup
_code_to_sym: dict[str, str] = {}
_sym_to_code: dict[str, str] = {}
_loaded = False


def _load(path: Path = _DATA_PATH) -> None:
    global _code_to_sym, _sym_to_code, _loaded
    _code_to_sym, _sym_to_code = {}, {}
    try:
        with open(path, newline="") as f:
            for row in csv.DictReader(f):
                code = row.get("scrip_code", "").strip()
                sym  = row.get("symbol", "").strip().upper()
                if code and sym:
                    _code_to_sym[code] = sym
                    # First mapping wins (some symbols have multiple scrip codes)
                    _sym_to_code.setdefault(sym, code)
        _loaded = True
        logger.debug("BSE scrip master loaded: %d entries", len(_code_to_sym))
    except FileNotFoundError:
        logger.warning("BSE scrip master not found at %s — run bse_scrip.refresh()", path)


def _ensure_loaded() -> None:
    if not _loaded:
        _load(_DATA_PATH)


# ── Public API ────────────────────────────────────────────────────────────────

def scrip_to_symbol(scrip_code: int | str) -> Optional[str]:
    """Return the NSE-compatible symbol for a BSE scrip code, or None."""
    _ensure_loaded()
    return _code_to_sym.get(str(scrip_code))


def symbol_to_scrip(symbol: str) -> Optional[str]:
    """Return the BSE scrip code for a symbol, or None."""
    _ensure_loaded()
    return _sym_to_code.get(symbol.upper())


def refresh(url: str = _SHOONYA_URL, dest: Path = _DATA_PATH) -> int:
    """Download a fresh BSE symbol master from Shoonya and update the CSV.

    Returns the number of equity rows written, or 0 on failure.
    """
    global _loaded
    try:
        import urllib.request
        with urllib.request.urlopen(url, timeout=15) as resp:
            data = resp.read()

        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            name = next(n for n in zf.namelist() if n.endswith(".txt"))
            content = zf.read(name).decode("utf-8", errors="replace")

        rows = list(csv.DictReader(io.StringIO(content)))
        equity = [
            r for r in rows
            if r.get("Instrument", "").strip() in _EQUITY_GROUPS
        ]

        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(dest, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["scrip_code", "symbol"])
            for r in equity:
                code = r.get("Token", "").strip()
                sym  = r.get("Symbol", "").strip()
                if code and sym:
                    w.writerow([code, sym])

        _loaded = False  # force reload on next lookup
        _load(dest)
        logger.info("BSE scrip master refreshed: %d equity entries", len(equity))
        return len(equity)

    except Exception as e:
        logger.warning("BSE scrip master refresh failed: %s", e)
        return 0
