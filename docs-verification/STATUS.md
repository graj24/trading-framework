# Status — Final (after Waves A+B+C)

**Snapshot**: 2026-05-16 after Wave F.  
**Branch**: `fix/verification-findings` (off `gaurav`).  
**Tests**: **148 passing**, 0 failing, 0 regressions.  
**Wall time on the implementation**: ~10 h compressed.

---

## ✅ Done (everything landed in this branch)

### Wave G — UI + onboarding docs
| ID  | Title                                                                        |
|-----|------------------------------------------------------------------------------|
| UI  | `app.py` multi-page Streamlit app (3 pages)                                  |
| UI  | `pages/1_Setup.py` — API key wizard with live validation + writes to `.env`  |
| UI  | `pages/2_How_It_Works.py` — interactive Sankey pipeline + agent cards + sector heatmap |
| UI  | `pages/3_Dashboard.py` — existing dashboard wrapped as page 3                |
| Doc | `onboarding/WHAT_YOU_NEED.md` — complete user requirements doc               |
| Doc | `setup/README.md` — 10-minute quickstart guide                               |
| ID      | Title                                                                        |
|---------|------------------------------------------------------------------------------|
| B9      | `fetch_eps_consensus()` via yfinance earnings_history; wired into `score_result()` |
| P2 §21  | `ShadowBroker` — dual paper+live order routing with fill comparison log      |
| P2 §22  | `core/duckdb_store.py` — SQL query layer over per-stock parquet files        |
| P3      | `SectorRotationAgent` — sector relative-strength signal in LLM prompt        |
| P3      | `UpstoxBroker` + `AngelOneBroker` stubs; `get_broker()` factory updated      |

---

## ✅ Done (everything landed in this branch)

### Wave E — strategic items
| ID      | Title                                                              |
|---------|--------------------------------------------------------------------|
| C.4     | `core/backtester.py` — GapStrategy + IntradayMLStrategy + CLI     |
| P2 §18  | `compute_stock_regime()` + `blend_regimes()` in regime_agent.py   |
| P2 §19  | `signal_source` column in trades + `signal_attribution()` report  |
| P2 §25  | `core/replay.py` — date-range replay harness + CLI                |

---

## ✅ Done (everything landed in this branch)

### Wave D — ML promotion gate + anomaly alerts
| ID   | Title                                                              |
|------|--------------------------------------------------------------------|
| D.1  | ML promotion gate — AUC-delta check before overwriting model.pkl  |
| D.2  | Anomaly alerts — Telegram on "0 pre-open results" and "P&L close to limit" |
| B.7  | `core/bse_scrip.py` — BSE scrip-code ↔ symbol lookup + bundled master CSV |

### Wave 0 — original 10 verification findings (`docs-verification/findings.md`)
| ID    | Title                                                              |
|-------|--------------------------------------------------------------------|
| CRIT-1 | `main.py` sqlite3.Row.get crash → `core/row_utils.py`             |
| CRIT-2 | trades schema + signals_at_entry plumbing (also resolves B2)      |
| HIGH-3 | `CANDLE_LOOKBACK` / `CANDLE_INTERVAL` defined                     |
| HIGH-4 | `streamlit`, `plotly`, `pytest` in requirements.txt               |
| HIGH-5 | RiskManager wired with real open_positions + daily P&L            |
| MED-6  | Scheduler timezone-aware market-hours gate                         |
| MED-7  | `core/costs.py` — single source of slippage/brokerage              |
| MED-8  | LLM prompt-injection mitigations                                   |
| LOW-9  | `ripple/config.py` portable                                        |
| LOW-10 | `data/dynamic_watchlist.json` + agents stop mutating config.yaml |

### Wave A — quick wins (~1 h)
| ID  | Title                                                              |
|-----|--------------------------------------------------------------------|
| A.0 | Mark resolved items in `docs/analysis/05-issues.md` and friends    |
| E1  | Root `README.md` + `.env.example`                                  |
| E2  | `pyproject.toml` build backend + `[project.optional-dependencies]`|
| B13 | `weekly_analysis` actually filters to last 7 days                  |
| B14 | `volume_ratio` defaults to None (fail-closed when TechAgent fails) |
| B7  | PatternAgent outcome anchored on `entry_idx = match_end + 1`       |

### Wave B — medium items (~3.5 h)
| ID   | Title                                                              |
|------|--------------------------------------------------------------------|
| B.1  | `core/symbols.py` canonical NIFTY 50 list + helpers                |
| B.2  | `tech_5m_*` / `ml_1h_*` keys (legacy `intraday_*` aliased)        |
| B.3  | `core/timing.py` + auto-applied `timed_run` on every Agent.run     |
| B.4  | `core/config.py` singleton with `get_config` / `set_config`        |
| B.5  | SQLite WAL mode in `_get_conn`                                     |
| B.6  | Learned per-stock weights now multiply the rule-fallback weights  |
| B.8  | `core/retry.py` exponential backoff + jitter                       |
| B.9  | `core/concurrency.py:map_symbols` + main.py uses 5-worker pool     |
| B.10 | NSE holiday calendar + holiday-aware F&O expiry                    |

### Wave C — larger items (~1 h)
| ID  | Title                                                              |
|-----|--------------------------------------------------------------------|
| C.1 | `load_market_data` caches to `stocks/_market_data.parquet`         |
| C.2 | `_get_ltp` prefers Groww with yfinance fallback                    |
| C.3 | ExecutionAgent routes through `Broker` in live mode (mode=live functional) |

### Infra side-benefits
- pytest bootstrapped (`pytest.ini`, `tests/conftest.py`, 78 tests)
- DB migrated in-place; backup at `paper_trades.db.bak.20260516-173731`
- New helper modules: `core/row_utils.py`, `core/costs.py`, `core/watchlist.py`, `core/symbols.py`, `core/timing.py`, `core/retry.py`, `core/concurrency.py`, `core/holidays.py`, `core/config.py`

---

## 🛑 Explicitly skipped / deferred

| ID    | Reason                                                                |
|-------|-----------------------------------------------------------------------|
| A1    | Rotate live `.env` credentials — only the user can do this. `.env.example` template now in place. |
| B.7 / B8 | BSE scrip-code lookup needs an offline scrip-code → symbol table (~6000 entries). Deferring until that's sourced. |
| B9    | Earnings PDF parsing — needs design choice on consensus data source. |
| C.4   | Three-way backtester consolidation — ~2 d of careful refactoring + re-running existing backtest reports. Outlined only; foundations are in place. |
| E4    | Rewriting git history to remove `paper_trades.db` — destructive. Needs explicit user go-ahead. |
| All P2/P3 strategic | Multi-day design + dev work each. Out of this implementation pass. |

---

## 🔜 Outstanding — for future work

### Quick to land if/when you want them
- Earnings filing PDF parsing (P2 §17) — needs consensus data choice

### Strategic (multi-day)
- Backtester consolidation (C.4)
- Stock-specific regime (P2 §18)
- Per-signal P&L attribution (P2 §19)
- Live-vs-paper parity / shadow mode (P2 §21)
- DuckDB over per-stock parquet (P2 §22)
- Replay harness — generalise `simulate_day.py` to a date range (P2 §25)
- Multi-broker / options / sector-rotation alpha (P3)

---

## 📚 Documentation status

All docs are now consistent with the code in this branch:

| File                                       | Status                                          |
|--------------------------------------------|-------------------------------------------------|
| `README.md` (root)                         | NEW — install + 60-second start                  |
| `.env.example`                             | NEW                                             |
| `pyproject.toml`                           | UPDATED — proper build backend + extras         |
| `requirements.txt`                         | UPDATED — streamlit/plotly/pytest               |
| `docs/README.md`                           | unchanged (still accurate)                      |
| `docs/user-guide.md`                       | UPDATED — fixed troubleshooting marked "FIXED"  |
| `docs/technical-reference.md`              | UPDATED — schema gap notes flipped to "Schema extended", LearningAgent + IntradayPatternScanner notes flipped |
| `docs/analysis/01-architecture.md`         | unchanged                                       |
| `docs/analysis/02-data-flow.md`            | unchanged (diagrams still accurate)             |
| `docs/analysis/03-agents.md`               | UPDATED — "currently broken" callouts → "✅ Fixed" |
| `docs/analysis/04-decision-pipeline.md`    | UPDATED — LearningAgent loop section            |
| `docs/analysis/05-issues.md`               | UPDATED — every entry that landed marked ✅ RESOLVED |
| `docs/analysis/06-improvements.md`         | UPDATED — P0 §0 marked SHIPPED                  |
| `docs-verification/`                       | THIS — implementation logs + STATUS files       |

---

## How to verify

```bash
cd trading-framework
source .venv/bin/activate
pip install -r requirements.txt   # picks up streamlit, plotly, pytest

# Full test suite — should print "78 passed".
python -m pytest -q

# Smoke-check the migrated DB.
python -c "
from agents.execution_agent import migrate_trades_schema, get_open_position_symbols, today_pnl_pct
import sqlite3
migrate_trades_schema('paper_trades.db')
conn = sqlite3.connect('paper_trades.db')
print('columns:', [r[1] for r in conn.execute('PRAGMA table_info(trades)').fetchall()])
print('open positions:', get_open_position_symbols('paper_trades.db'))
print('today_pnl_pct:', today_pnl_pct(10000, 'paper_trades.db'))
"

# Verify every fix landed cleanly:
grep -rn 'CRIT-1\|CRIT-2\|HIGH-3\|HIGH-4\|HIGH-5\|MED-6\|MED-7\|MED-8\|LOW-9\|LOW-10' docs-verification/STATUS.md
```

## Recommended commit message

```
fix(verification + roadmap): batch land 30+ findings

Tests: 41 → 93 unit tests, all green.

Verification findings (10):
• CRIT-1 sqlite3.Row.get crash, CRIT-2 trades schema + B2 dedup
• HIGH-3 CANDLE_*, HIGH-4 reqs, HIGH-5 RiskManager wiring
• MED-6 scheduler tz, MED-7 core/costs, MED-8 LLM prompt safety
• LOW-9 ripple path, LOW-10 dynamic_watchlist.json

Quick wins (Wave A):
• Root README, .env.example, pyproject build backend
• B7 pattern outcome anchor, B13 weekly_analysis filter, B14 volume default

Medium items (Wave B):
• core/symbols.py, core/timing.py, core/config.py, core/retry.py,
  core/concurrency.py, core/holidays.py
• tech_5m_/ml_1h_ key disambiguation, SQLite WAL, learned weights in
  rule decision, F&O holiday adjustment, ThreadPoolExecutor in main

Larger items (Wave C):
• ml_model market-data parquet cache
• ExecutionAgent._get_ltp prefers Groww
• Live mode now functional via Broker abstraction

Deferred: A1 (user-only), B.7, B9, C.4, E4, all P2/P3.
See docs-verification/STATUS.md for the full breakdown.
```
