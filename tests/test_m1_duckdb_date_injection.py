"""M-1: _date_where must reject non-ISO-date strings (SQL injection guard)."""
import pytest
from core.duckdb_store import _date_where


def test_valid_dates_accepted():
    clause = _date_where("date", "2024-01-01", "2024-12-31")
    assert "2024-01-01" in clause
    assert "2024-12-31" in clause


def test_none_bounds_accepted():
    assert _date_where("date", None, None) == ""
    assert "2024-01-01" in _date_where("date", "2024-01-01", None)


def test_injection_start_raises():
    with pytest.raises(ValueError, match="Invalid date"):
        _date_where("date", "2024' OR 1=1; --", None)


def test_injection_end_raises():
    with pytest.raises(ValueError, match="Invalid date"):
        _date_where("date", None, "2024-12-31'; DROP TABLE x; --")


def test_non_date_string_raises():
    with pytest.raises(ValueError, match="Invalid date"):
        _date_where("date", "not-a-date", None)
