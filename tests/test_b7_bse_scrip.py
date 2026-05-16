"""Tests for core/bse_scrip.py — BSE scrip-code ↔ symbol lookup."""
from __future__ import annotations

import csv
import io
import zipfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest


# ── fixtures ──────────────────────────────────────────────────────────────────

def _write_csv(path: Path, rows: list[tuple[str, str]]) -> None:
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["scrip_code", "symbol"])
        for code, sym in rows:
            w.writerow([code, sym])


@pytest.fixture(autouse=True)
def _reset_cache():
    """Reset module-level cache between tests."""
    import core.bse_scrip as bs
    bs._code_to_sym = {}
    bs._sym_to_code = {}
    bs._loaded = False
    yield
    bs._code_to_sym = {}
    bs._sym_to_code = {}
    bs._loaded = False


def _patch_and_load(monkeypatch, tmp_csv: Path) -> "core.bse_scrip":
    """Monkeypatch _DATA_PATH and force a fresh load from tmp_csv."""
    import core.bse_scrip as bs
    monkeypatch.setattr(bs, "_DATA_PATH", tmp_csv)
    bs._loaded = False
    bs._code_to_sym = {}
    bs._sym_to_code = {}
    return bs


# ── basic lookups ─────────────────────────────────────────────────────────────

class TestLookups:
    def test_scrip_to_symbol_known(self, tmp_path, monkeypatch):
        p = tmp_path / "master.csv"
        _write_csv(p, [("500325", "RELIANCE"), ("532540", "TCS")])
        bs = _patch_and_load(monkeypatch, p)
        assert bs.scrip_to_symbol("500325") == "RELIANCE"
        assert bs.scrip_to_symbol(532540) == "TCS"

    def test_scrip_to_symbol_unknown_returns_none(self, tmp_path, monkeypatch):
        p = tmp_path / "master.csv"
        _write_csv(p, [("500325", "RELIANCE")])
        bs = _patch_and_load(monkeypatch, p)
        assert bs.scrip_to_symbol("999999") is None

    def test_symbol_to_scrip_known(self, tmp_path, monkeypatch):
        p = tmp_path / "master.csv"
        _write_csv(p, [("500325", "RELIANCE"), ("500209", "INFY")])
        bs = _patch_and_load(monkeypatch, p)
        assert bs.symbol_to_scrip("RELIANCE") == "500325"
        assert bs.symbol_to_scrip("infy") == "500209"   # case-insensitive

    def test_symbol_to_scrip_unknown_returns_none(self, tmp_path, monkeypatch):
        p = tmp_path / "master.csv"
        _write_csv(p, [("500325", "RELIANCE")])
        bs = _patch_and_load(monkeypatch, p)
        assert bs.symbol_to_scrip("NOSUCHSYM") is None

    def test_missing_file_returns_none_gracefully(self, tmp_path, monkeypatch):
        bs = _patch_and_load(monkeypatch, tmp_path / "nonexistent.csv")
        assert bs.scrip_to_symbol("500325") is None
        assert bs.symbol_to_scrip("RELIANCE") is None

    def test_int_scrip_code_works(self, tmp_path, monkeypatch):
        p = tmp_path / "master.csv"
        _write_csv(p, [("500325", "RELIANCE")])
        bs = _patch_and_load(monkeypatch, p)
        assert bs.scrip_to_symbol(500325) == "RELIANCE"

    def test_duplicate_symbol_first_code_wins(self, tmp_path, monkeypatch):
        """When a symbol maps to multiple scrip codes, first row wins."""
        p = tmp_path / "master.csv"
        _write_csv(p, [("111111", "RELIANCE"), ("222222", "RELIANCE")])
        bs = _patch_and_load(monkeypatch, p)
        assert bs.symbol_to_scrip("RELIANCE") == "111111"


# ── bundled data sanity ───────────────────────────────────────────────────────

class TestBundledData:
    def test_bundled_csv_has_known_stocks(self):
        """The bundled data/bse_scrip_master.csv must contain RELIANCE and TCS."""
        import core.bse_scrip as bs
        # Use the real bundled file (no monkeypatch)
        assert bs.scrip_to_symbol("500325") == "RELIANCE"
        assert bs.scrip_to_symbol("532540") == "TCS"
        assert bs.symbol_to_scrip("RELIANCE") == "500325"

    def test_bundled_csv_has_reasonable_size(self):
        import core.bse_scrip as bs
        bs._ensure_loaded()
        assert len(bs._code_to_sym) >= 1000, "Expected at least 1000 equity entries"


# ── refresh ───────────────────────────────────────────────────────────────────

class TestRefresh:
    def _make_zip(self, rows: list[tuple]) -> bytes:
        """Build a fake Shoonya zip with the given rows."""
        content = "Exchange,Token,LotSize,Symbol,TradingSymbol,Instrument,TickSize,\n"
        for exchange, token, symbol, instrument in rows:
            content += f"{exchange},{token},1,{symbol},{symbol},{instrument},0.05,\n"
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("BSE_symbols.txt", content)
        return buf.getvalue()

    def test_refresh_writes_csv_and_updates_cache(self, tmp_path, monkeypatch):
        dest = tmp_path / "master.csv"
        bs = _patch_and_load(monkeypatch, dest)

        zip_data = self._make_zip([
            ("BSE", "500325", "RELIANCE", "A"),
            ("BSE", "532540", "TCS", "A"),
            ("BSE", "977783", "SOMEBOND", "F"),  # non-equity, should be excluded
        ])

        mock_resp = MagicMock()
        mock_resp.read.return_value = zip_data
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            count = bs.refresh(dest=dest)

        assert count == 2
        assert dest.exists()
        assert bs.scrip_to_symbol("500325") == "RELIANCE"
        assert bs.scrip_to_symbol("977783") is None  # bond excluded

    def test_refresh_failure_returns_zero(self, tmp_path, monkeypatch):
        dest = tmp_path / "master.csv"
        bs = _patch_and_load(monkeypatch, dest)

        with patch("urllib.request.urlopen", side_effect=OSError("network down")):
            count = bs.refresh(dest=dest)

        assert count == 0
