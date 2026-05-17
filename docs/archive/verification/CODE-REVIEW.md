# Code Review — Post-Merge Implementations

> Logical-correctness review of work that landed in `feat/bloomberg-ui` after
> the verification-findings session.
> Started: 2026-05-17 00:06 IST.

## Scope

| # | Area                                | File(s)                                                |
|---|-------------------------------------|--------------------------------------------------------|
| 1 | BSE scrip-code lookup               | `core/bse_scrip.py` + `tests/test_b7_bse_scrip.py`    |
| 2 | Backtester consolidation             | `core/backtester.py` + `tests/test_wave_e_strategic.py` |
| 3 | Replay harness                       | `core/replay.py` + tests                              |
| 4 | DuckDB store                         | `core/duckdb_store.py` + `tests/test_wave_f_remaining.py` |
| 5 | EPS consensus / earnings parsing     | wave_f tests + agents/earnings_calendar_agent.py      |
| 6 | Stock-specific regime                | agents/regime_agent.py + wave_e                       |
| 7 | Per-signal P&L attribution           | agents/execution_agent.py + wave_e                    |
| 8 | Shadow mode (paper-vs-live parity)   | wave_f tests                                           |
| 9 | ML promotion gate                    | `tests/test_p2_ml_promotion_gate.py`                   |
| 10| Anomaly alerts                       | `tests/test_p2_anomaly_alerts.py`                      |
| 11| Sector rotation / multi-broker (P3)  | wave_f tests                                           |
| 12| Bloomberg UI                         | `ui/app.py`, `ui/pages/*`                             |

## Legend

- ✅ correct & defensible
- ⚠️ correct but with caveats / edge cases worth noting
- ❌ logical bug / regression / missing case
- 💡 suggestion (not a defect)

---

(populated below as the review proceeds)

---

## Findings

### 🔴 Bugs

#### B-1. `core/replay.py:_gap_signal` — MACD filter computed but never applied
**Where**: `core/replay.py` lines ~110–120.
**What**: The function computes `macd_bull` but never uses it as a filter:
```python
macd_bull = float(macd.iloc[-1]) > float((macd - macd.ewm(span=9, adjust=False).mean()).iloc[-1])
# ... then only volume and EMA50 filters are checked. macd_bull is ignored.
```
The corresponding `GapStrategy` in `core/backtester.py` does apply the MACD filter:
```python
df["macd_bull"]  = (macd - macd.ewm(span=9, adjust=False).mean()) > 0
# ...
if not row["macd_bull"]: continue
```
**Severity**: 🔴 — replay output is **NOT equivalent** to `GapStrategy` output. The replay
will produce strictly more signals (MACD filter removed). This breaks the docstring
contract ("Gap signal — same logic as GapStrategy") and biases replay reports upward.

**Plus** the formula itself is non-standard. `macd > histogram` (i.e. `macd > macd - signal`)
reduces to `signal > 0`, which is "is signal line positive" — different from the standard
"MACD line above signal line" check (`histogram > 0`).

**Fix**:
```python
macd_bull = (macd - macd.ewm(span=9, adjust=False).mean()).iloc[-1] > 0
# ...
if row["Volume"] < vol_avg20 * 1.5: return None
if prev_row["Close"] < ema50:        return None
if not macd_bull:                    return None    # ← add this line
```

---

### 🟠 Medium issues

#### M-1. `core/duckdb_store.py:_date_where` — SQL injection via start/end strings
**Where**: `core/duckdb_store.py` lines ~150–155.
**What**: Date strings are interpolated directly into SQL:
```python
parts.append(f"CAST({col} AS DATE) >= DATE '{start}'")
```
A caller passing `start="2024' OR 1=1; --"` would inject. Since DuckDB only reads parquet
files via `read_parquet()`, the data layer is read-only — but the query could still leak
arbitrary parquet content or DOS the connection.

**Severity**: 🟠 — low immediate risk (no UI exposes this), but the pattern is wrong.
**Fix**: use parameterised queries — DuckDB supports `?` placeholders. Or validate `start/end`
against a date regex before interpolation.

#### M-2. `core/duckdb_store.py:STOCKS_DIR` is cwd-relative
**Where**: `core/duckdb_store.py:25` — `STOCKS_DIR = Path("stocks")`.
**What**: Inconsistent with `core/knowledge_base.py:STOCKS_DIR = Path(__file__).parent.parent / "stocks"`,
which is absolute. If the user runs DuckDB queries from a different cwd, results will silently
diverge.
**Severity**: 🟠.
**Fix**: import `STOCKS_DIR` from `core.knowledge_base` and reuse.

#### M-3. ML promotion gate — accepts AUC=0 model when incumbent is also 0
**Where**: `ml_model.py:_save_if_better` (and same logic in `india_intraday_model.py`).
**What**: `_incumbent_auc` returns `0.0` when no model exists. Then
`new_auc < 0.0 + (-0.02) = -0.02` is False for any non-negative AUC, so a degenerate
new model with `auc=0.0` (e.g. trained on single-class y) would still be promoted.
**Severity**: 🟠 (rare edge case but trivial to fix).
**Fix**: add a hard floor: `if new_auc < 0.55: reject`.

#### M-4. ML promotion — no `*_prev.pkl` for rollback
**Where**: `ml_model.py:_save_if_better`, `india_intraday_model.py:_save_if_better`.
**What**: When promoting a new model, the previous `.pkl` is overwritten in place.
The implementation note in `06-improvements.md` P2 §23 explicitly mentioned keeping
the previous model — that's missing.
**Severity**: 🟠 (operational).
**Fix**: before writing, `os.rename(MODEL_PATH, MODEL_PATH.with_suffix('.prev.pkl'))`.

#### M-5. `ShadowBroker._fill_log` grows unboundedly
**Where**: `core/broker.py:ShadowBroker.place_order`.
**What**: Every order appends to `self._fill_log`. A long-running daemon will accumulate
indefinitely.
**Severity**: 🟠 (memory leak in long-running processes).
**Fix**: cap to last N entries (e.g. 1000), or rotate to disk hourly.

---

### 🟡 Minor / nits

#### N-1. `core/replay.py` only runs gap strategy
**Where**: `core/replay.py`.
**What**: Docstring claims it "generalises simulate_day.py" — but `simulate_day.py` exercises
the **full master pipeline** (technical, news, pattern, regime, ML, LLM). The replay
implements **only** the gap strategy. That's narrower than advertised.
**Fix**: either rename to `replay_gap.py` or extend to plug in arbitrary strategies (similar
to `Strategy` ABC in `core/backtester.py`).

#### N-2. No test verifies replay-vs-GapStrategy equivalence
**Where**: `tests/test_wave_e_strategic.py`.
**What**: Tests verify replay produces SOME trades and writes to a separate DB. They
don't compare the trade set to what `GapStrategy` would produce on the same data —
which would have caught **B-1** above.
**Fix**: add a test that runs both on identical synthetic data and asserts equality
of the trade set (after applying B-1's fix).

#### N-3. `core/duckdb_store.py:_index_col` opens a fresh DuckDB connection per call
Minor perf nit. Each `symbol_history()` call opens 2 connections (one for schema, one
for data). Cache the schema info or pass an existing conn.

#### N-4. `core/replay.py` uses cwd-relative `replay_trades.db` default
**Where**: `core/replay.py:31` — `REPLAY_DB = Path("replay_trades.db")`.
**What**: Same cwd-relative issue as M-2. Tests pass because they pass `db_path=...`
explicitly.
**Fix**: `REPLAY_DB = Path(__file__).parent.parent / "replay_trades.db"`.

#### N-5. `agents/execution_agent.py:signal_attribution` falsy check
**Where**: line ~423: `src = (row["signal_source"] or "unknown") if "signal_source" in row.keys() else "unknown"`.
**What**: `or "unknown"` triggers on any falsy (empty string, 0, None). For a string column
"unknown" is fine, but if a future change uses 0/empty differently this will be confusing.
Functionally correct today.

#### N-6. `ui/pages/3_Dashboard.py` is only 21 lines
**Where**: `ui/pages/3_Dashboard.py`.
**What**: Looks like a stub or minimal redirect to the existing dashboard. Not a bug —
just flagging as "low content" in case it was meant to be a richer page.

#### N-7. `core/broker.py:get_broker` default to Zerodha when `broker` key missing
**Where**: line ~177 — `broker = config.get("trading", {}).get("broker", "zerodha").lower()`.
**What**: If a user is on Upstox and forgets to set `trading.broker`, they'd get
`ZerodhaBroker(api_key="", access_token="")` and a confusing error from kiteconnect
later. Could fail-fast with a clearer error.

---

### ✅ Things that look correct

- **`core/bse_scrip.py`** — clean module, lazy load, idempotent refresh, 4868 bundled rows,
  18 tests cover edge cases.
- **`core/backtester.py:GapStrategy`** — preserves original `backtest_gap.py` behaviour; uses
  the centralised `core.costs` constants; applies all four filters (gap threshold + volume +
  EMA50 + MACD).
- **`core/backtester.py:IntradayMLStrategy`** — lazy model loading, market context loaded once,
  trailing stop logic is correct (only ratchets up).
- **`agents/regime_agent.py:compute_stock_regime`** — same regime classification thresholds as
  the NIFTY-level path, no network calls, returns `source="stock"` for traceability.
- **`agents/regime_agent.py:blend_regimes`** — convex combination on a regime-priority scale,
  with `aligned/divergent/nifty_only` notes.
- **`agents/earnings_calendar_agent.py:fetch_eps_consensus`** — pulls from `t.earnings_history`,
  classifies with a clear ±5% threshold, soft-fails to UNKNOWN.
- **`agents/execution_agent.py:signal_attribution`** — groups by source, computes win-rate / avg /
  total P&L, sorted by P&L, excludes open trades.
- **`core/scheduler.py` anomaly alerts** — 0-result pre-open scan triggers ANOMALY; daily P&L at
  75% of `max_loss_per_day_pct` triggers P&L alert.
- **`core/broker.py:ShadowBroker`** — sends to paper always, live optionally, logs divergence,
  10-bps threshold for warnings, returns paper order id (transparent to callers).
- **Multi-broker stubs** (`UpstoxBroker`, `AngelOneBroker`) — clean NotImplementedError with
  install hints.
- **148 tests pass.** Test density per feature is reasonable.

---

## Verdict

The post-merge implementation is **largely solid**. One real correctness bug (**B-1** in
`replay.py` MACD filter), four medium issues that should be addressed before this code goes
live, and a handful of minor nits.

Nothing here is a project-blocker — but **B-1** specifically means any backtest report
generated via `core/replay.py` is biased and should be re-run after the fix.

Recommended order of fixes:

1. **B-1** (replay MACD filter) — 5 min.
2. **M-3 + M-4** (ML promotion sanity floor + backup) — 30 min.
3. **M-2** (DuckDB STOCKS_DIR consistency) — 10 min.
4. **M-1** (DuckDB SQL parameterisation) — 30 min.
5. **M-5** (ShadowBroker fill-log cap) — 10 min.
6. The minor / nits, as time permits.

Total: ~2 h to clean everything up.

---

## Findings

### 🔴 Bugs

#### B-1. `core/replay.py:_gap_signal` — MACD filter computed but never applied
**Where**: `core/replay.py` lines ~110–120.
**What**: The function computes `macd_bull` but never uses it as a filter:
```python
macd_bull = float(macd.iloc[-1]) > float((macd - macd.ewm(span=9, adjust=False).mean()).iloc[-1])
# ... then only volume and EMA50 filters are checked. macd_bull is ignored.
```
The corresponding `GapStrategy` in `core/backtester.py` does apply the MACD filter:
```python
df["macd_bull"]  = (macd - macd.ewm(span=9, adjust=False).mean()) > 0
# ...
if not row["macd_bull"]: continue
```
**Severity**: 🔴 — replay output is **NOT equivalent** to `GapStrategy` output. The replay
will produce strictly more signals (MACD filter removed). This breaks the docstring
contract ("Gap signal — same logic as GapStrategy") and biases replay reports upward.

**Plus** the formula itself is non-standard. `macd > histogram` (i.e. `macd > macd - signal`)
reduces to `signal > 0`, which is "is signal line positive" — different from the standard
"MACD line above signal line" check (`histogram > 0`).

**Fix**:
```python
macd_bull = (macd - macd.ewm(span=9, adjust=False).mean()).iloc[-1] > 0
# ...
if row["Volume"] < vol_avg20 * 1.5: return None
if prev_row["Close"] < ema50:        return None
if not macd_bull:                    return None    # ← add this line
```

---

### 🟠 Medium issues

#### M-1. `core/duckdb_store.py:_date_where` — SQL injection via start/end strings
**Where**: `core/duckdb_store.py` lines ~150–155.
**What**: Date strings are interpolated directly into SQL:
```python
parts.append(f"CAST({col} AS DATE) >= DATE '{start}'")
```
A caller passing `start="2024' OR 1=1; --"` would inject. Since DuckDB only reads parquet
files via `read_parquet()`, the data layer is read-only — but the query could still leak
arbitrary parquet content or DOS the connection.

**Severity**: 🟠 — low immediate risk (no UI exposes this), but the pattern is wrong.
**Fix**: use parameterised queries — DuckDB supports `?` placeholders. Or validate `start/end`
against a date regex before interpolation.

#### M-2. `core/duckdb_store.py:STOCKS_DIR` is cwd-relative
**Where**: `core/duckdb_store.py:25` — `STOCKS_DIR = Path("stocks")`.
**What**: Inconsistent with `core/knowledge_base.py:STOCKS_DIR = Path(__file__).parent.parent / "stocks"`,
which is absolute. If the user runs DuckDB queries from a different cwd, results will silently
diverge.
**Severity**: 🟠.
**Fix**: import `STOCKS_DIR` from `core.knowledge_base` and reuse.

#### M-3. ML promotion gate — accepts AUC=0 model when incumbent is also 0
**Where**: `ml_model.py:_save_if_better` (and same logic in `india_intraday_model.py`).
**What**: `_incumbent_auc` returns `0.0` when no model exists. Then
`new_auc < 0.0 + (-0.02) = -0.02` is False for any non-negative AUC, so a degenerate
new model with `auc=0.0` (e.g. trained on single-class y) would still be promoted.
**Severity**: 🟠 (rare edge case but trivial to fix).
**Fix**: add a hard floor: `if new_auc < 0.55: reject`.

#### M-4. ML promotion — no `*_prev.pkl` for rollback
**Where**: `ml_model.py:_save_if_better`, `india_intraday_model.py:_save_if_better`.
**What**: When promoting a new model, the previous `.pkl` is overwritten in place.
The implementation note in `06-improvements.md` P2 §23 explicitly mentioned keeping
the previous model — that's missing.
**Severity**: 🟠 (operational).
**Fix**: before writing, `os.rename(MODEL_PATH, MODEL_PATH.with_suffix('.prev.pkl'))`.

#### M-5. `ShadowBroker._fill_log` grows unboundedly
**Where**: `core/broker.py:ShadowBroker.place_order`.
**What**: Every order appends to `self._fill_log`. A long-running daemon will accumulate
indefinitely.
**Severity**: 🟠 (memory leak in long-running processes).
**Fix**: cap to last N entries (e.g. 1000), or rotate to disk hourly.

---

### 🟡 Minor / nits

#### N-1. `core/replay.py` only runs gap strategy
**Where**: `core/replay.py`.
**What**: Docstring claims it "generalises simulate_day.py" — but `simulate_day.py` exercises
the **full master pipeline** (technical, news, pattern, regime, ML, LLM). The replay
implements **only** the gap strategy. That's narrower than advertised.
**Fix**: either rename to `replay_gap.py` or extend to plug in arbitrary strategies (similar
to `Strategy` ABC in `core/backtester.py`).

#### N-2. No test verifies replay-vs-GapStrategy equivalence
**Where**: `tests/test_wave_e_strategic.py`.
**What**: Tests verify replay produces SOME trades and writes to a separate DB. They
don't compare the trade set to what `GapStrategy` would produce on the same data —
which would have caught **B-1** above.
**Fix**: add a test that runs both on identical synthetic data and asserts equality
of the trade set (after applying B-1's fix).

#### N-3. `core/duckdb_store.py:_index_col` opens a fresh DuckDB connection per call
Minor perf nit. Each `symbol_history()` call opens 2 connections (one for schema, one
for data). Cache the schema info or pass an existing conn.

#### N-4. `core/replay.py` uses cwd-relative `replay_trades.db` default
**Where**: `core/replay.py:31` — `REPLAY_DB = Path("replay_trades.db")`.
**What**: Same cwd-relative issue as M-2. Tests pass because they pass `db_path=...`
explicitly.
**Fix**: `REPLAY_DB = Path(__file__).parent.parent / "replay_trades.db"`.

#### N-5. `agents/execution_agent.py:signal_attribution` falsy check
**Where**: line ~423: `src = (row["signal_source"] or "unknown") if "signal_source" in row.keys() else "unknown"`.
**What**: `or "unknown"` triggers on any falsy (empty string, 0, None). For a string column
"unknown" is fine, but if a future change uses 0/empty differently this will be confusing.
Functionally correct today.

#### N-6. `ui/pages/3_Dashboard.py` is only 21 lines
**Where**: `ui/pages/3_Dashboard.py`.
**What**: Looks like a stub or minimal redirect to the existing dashboard. Not a bug —
just flagging as "low content" in case it was meant to be a richer page.

#### N-7. `core/broker.py:get_broker` default to Zerodha when `broker` key missing
**Where**: line ~177 — `broker = config.get("trading", {}).get("broker", "zerodha").lower()`.
**What**: If a user is on Upstox and forgets to set `trading.broker`, they'd get
`ZerodhaBroker(api_key="", access_token="")` and a confusing error from kiteconnect
later. Could fail-fast with a clearer error.

---

### ✅ Things that look correct

- **`core/bse_scrip.py`** — clean module, lazy load, idempotent refresh, 4868 bundled rows,
  18 tests cover edge cases.
- **`core/backtester.py:GapStrategy`** — preserves original `backtest_gap.py` behaviour; uses
  the centralised `core.costs` constants; applies all four filters (gap threshold + volume +
  EMA50 + MACD).
- **`core/backtester.py:IntradayMLStrategy`** — lazy model loading, market context loaded once,
  trailing stop logic is correct (only ratchets up).
- **`agents/regime_agent.py:compute_stock_regime`** — same regime classification thresholds as
  the NIFTY-level path, no network calls, returns `source="stock"` for traceability.
- **`agents/regime_agent.py:blend_regimes`** — convex combination on a regime-priority scale,
  with `aligned/divergent/nifty_only` notes.
- **`agents/earnings_calendar_agent.py:fetch_eps_consensus`** — pulls from `t.earnings_history`,
  classifies with a clear ±5% threshold, soft-fails to UNKNOWN.
- **`agents/execution_agent.py:signal_attribution`** — groups by source, computes win-rate / avg /
  total P&L, sorted by P&L, excludes open trades.
- **`core/scheduler.py` anomaly alerts** — 0-result pre-open scan triggers ANOMALY; daily P&L at
  75% of `max_loss_per_day_pct` triggers P&L alert.
- **`core/broker.py:ShadowBroker`** — sends to paper always, live optionally, logs divergence,
  10-bps threshold for warnings, returns paper order id (transparent to callers).
- **Multi-broker stubs** (`UpstoxBroker`, `AngelOneBroker`) — clean NotImplementedError with
  install hints.
- **148 tests pass.** Test density per feature is reasonable.

---

## Verdict

The post-merge implementation is **largely solid**. One real correctness bug (**B-1** in
`replay.py` MACD filter), four medium issues that should be addressed before this code goes
live, and a handful of minor nits.

Nothing here is a project-blocker — but **B-1** specifically means any backtest report
generated via `core/replay.py` is biased and should be re-run after the fix.

Recommended order of fixes:

1. **B-1** (replay MACD filter) — 5 min.
2. **M-3 + M-4** (ML promotion sanity floor + backup) — 30 min.
3. **M-2** (DuckDB STOCKS_DIR consistency) — 10 min.
4. **M-1** (DuckDB SQL parameterisation) — 30 min.
5. **M-5** (ShadowBroker fill-log cap) — 10 min.
6. The minor / nits, as time permits.

Total: ~2 h to clean everything up.
