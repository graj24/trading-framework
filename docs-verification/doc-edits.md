# Suggested Edits to Existing Docs

> Patches to make to `docs/` to bring it to 100% accuracy. Listed by file.

---

## `docs/analysis/05-issues.md`

### Add a new B0 entry at the top of section B (Correctness bugs):

```markdown
### B0. 🔴 `main.py` crashes on every closed trade — sqlite3.Row has no `.get()`
**Where**: `main.py:135-138`. `closed_trades` rows are `sqlite3.Row` (because `conn.row_factory = sqlite3.Row`), and `sqlite3.Row` does not implement `.get()`. The current `paper_trades.db` already has 1 closed trade, so the next run of `main.py` raises `AttributeError` mid-loop — skipping the rest of the post-cycle reporting and the LearningAgent feedback entirely.
**Fix**: `t.get("k", default)` → `t["k"] if "k" in t.keys() else default` (or convert via `dict(t).get(...)`). See CRIT-1 in `docs-verification/findings.md`.

### B0b. 🔴 `trades` schema lacks columns the LearningAgent needs
**Where**: `agents/execution_agent.py:_get_conn()` DDL.
The CREATE TABLE has no `technical_score / sentiment / pattern_ev / sector_momentum / regime_alignment` columns, but `main.py:135-138` reads them via `t.get(...)` to feed `LearningAgent.update_weights`. Even after fixing B0, every value would be `None` / 0, so weights never change. The whole feedback loop is decorative.
**Fix**: add the columns; populate from a new `signals_at_entry` parameter in `ExecutionAgent.execute_trade`. See CRIT-2 in `docs-verification/findings.md`.

### B0c. 🟠 `IntradayPatternScanner.get_intraday_candles` references undefined globals
**Where**: `agents/intraday_scanner.py:109` uses `CANDLE_LOOKBACK` and `CANDLE_INTERVAL`. Neither is defined anywhere in the module (verified via repo-wide grep). The call is wrapped in a bare `try/except`, so the `NameError` is silently swallowed and the function always returns `None`. As a consequence, the **entire intraday pattern detection feature** produces zero signals today.
**Fix**: define `CANDLE_LOOKBACK = "2d"` and `CANDLE_INTERVAL = "5m"` at module top.
```

### Replace existing B15 with:

```markdown
### B15. (Subsumed by B0 / B0b above.)
Originally documented as "LearningAgent ignores `sector_momentum` and `regime_alignment`". The actual issue is structural: see B0 and B0b.
```

### Tighten C11:

```markdown
### C11. 🟢 `requirements.txt` has `# kiteconnect==5.0.1` commented but `core/broker.py` references it
The reference is **lazy** — `from kiteconnect import KiteConnect` is inside `ZerodhaBroker.__init__` with an explicit `ImportError` handler. So the module imports cleanly without kiteconnect; the issue is just that the optional-dependency story isn't formalised.
**Fix**: declare under `[project.optional-dependencies] live = ["kiteconnect>=5.0.1"]`.
```

### Tighten B6:

```markdown
### B6. 🟡 `monitor_positions()` uses a single LTP sample
**Where**: `agents/execution_agent.py:_get_ltp` calls `yf.Ticker(...).history(period="1d")`.
**Why**: during market hours, this is the live LTP — but only one sample per 5-minute monitor tick. SL/target touches that happen between ticks are missed. Outside market hours, yfinance returns the **previous close**, so SL/target evaluations are stale (mostly harmless because the market is closed, but worth noting).
**Fix**: use Groww `get_quote(symbol)` for live LTP; for SL/target, also walk yfinance 5m candles since entry to detect intraday touches between ticks.
```

---

## `docs/analysis/03-agents.md`

### §9 LearningAgent — append:

```markdown
> **Critical**: this agent is currently **doubly inert**. (1) `main.py` crashes when it tries to call `.get()` on `sqlite3.Row` (see Issue B0). (2) Even if patched, the `trades` table doesn't store entry-time signals, so `update_weights` always receives 0 values and never adjusts weights (Issue B0b).
```

### §12 IntradayPatternScanner — append:

```markdown
> **Currently broken**: `get_intraday_candles` references `CANDLE_LOOKBACK` and `CANDLE_INTERVAL`, which are never defined. The `NameError` is swallowed by a bare `try/except`, so the function always returns `None` and the scanner produces zero patterns. See Issue B0c.
```

---

## `docs/analysis/04-decision-pipeline.md`

### §5 (LearningAgent loop) — replace with:

```markdown
## 5. The LearningAgent loop (post-trade feedback)

Triggered by `main.py` after each cycle.

> **Note (2026-05-16)**: this loop currently raises `AttributeError` on the first closed trade due to a bug in `main.py` (see `docs/analysis/05-issues.md` §B0). When patched, the deeper issue is that the `trades` schema doesn't store entry-time signals, so the weights still never change (§B0b). Diagram below documents the *intended* loop.

```mermaid
sequenceDiagram
  ... (existing diagram) ...
```
```

---

## `docs/technical-reference.md`

### §3 (repo layout) — add a note about dashboard deps:

```markdown
> **Heads-up**: `dashboard.py` imports `streamlit` and `plotly`, which are NOT in `requirements.txt`. Install them separately or add to your environment. See user-guide §3.5.
```

### §5.10 LearningAgent — append:

```markdown
> Currently inert in production due to `main.py` bug + missing schema columns. See `analysis/05-issues.md` §B0/B0b.
```

### §5.11 IntradayPatternScanner — append:

```markdown
> Currently produces zero patterns due to undefined globals (`CANDLE_LOOKBACK`, `CANDLE_INTERVAL`) — the `NameError` is silently swallowed. See `analysis/05-issues.md` §B0c.
```

### §6.2 SQLite trade ledger — annotate:

```markdown
> **Schema gap**: the table doesn't include columns for entry-time signals (`technical_score`, `sentiment`, `pattern_ev`, `sector_momentum`, `regime_alignment`). `main.py` reads them as if they exist, which is the root cause of `analysis/05-issues.md` §B0/B0b.
```

---

## `docs/user-guide.md`

### §10 Troubleshooting — add:

```markdown
### 10.10. `AttributeError: 'sqlite3.Row' object has no attribute 'get'` after a closed trade
Known bug — `main.py:135-138` tries `t.get(...)` on a `sqlite3.Row`. Workarounds:
- Delete the closed trade temporarily: `sqlite3 paper_trades.db "DELETE FROM trades WHERE outcome != 'open'"` (you lose the trade history).
- Apply the patch in `docs-verification/findings.md` CRIT-1.

### 10.11. `IntradayPatternScanner` returns no signals even during market hours
Known bug — undefined `CANDLE_LOOKBACK`/`CANDLE_INTERVAL` globals. Apply HIGH-3 from `docs-verification/findings.md`:
```python
# Add near top of agents/intraday_scanner.py
CANDLE_LOOKBACK = "2d"
CANDLE_INTERVAL = "5m"
```
```

---

## `docs/analysis/06-improvements.md`

### Insert at the very top of the P0 section:

```markdown
### 0. Patch crashing / broken code paths (CRIT-1, CRIT-2, HIGH-3, HIGH-4). *(1.25d total)*
Found by the doc-verification pass. See `docs-verification/findings.md` for code patches.
- **CRIT-1**: `main.py` crashes on closed trades — `sqlite3.Row` has no `.get()`.
- **CRIT-2**: `trades` schema doesn't store entry-time signals → LearningAgent inert.
- **HIGH-3**: `agents/intraday_scanner.py` uses undefined `CANDLE_LOOKBACK`/`CANDLE_INTERVAL` → all intraday patterns broken.
- **HIGH-4**: `streamlit`/`plotly` missing from `requirements.txt`.

These are pre-existing latent bugs that were not caught when the original docs were written. Fix before any new feature work.
```

---

## Quick rerun command

To re-verify the docs after these edits:
```bash
# Spot-check: rerun the original verification greps
cd trading-framework
python3 -c "
import sqlite3
conn = sqlite3.connect('paper_trades.db')
conn.row_factory = sqlite3.Row
test = conn.execute('SELECT 1 AS x').fetchone()
try: test.get('k', 0); print('FIXED')
except AttributeError: print('STILL BROKEN — apply CRIT-1')
"

grep -n 'CANDLE_LOOKBACK\|CANDLE_INTERVAL' agents/intraday_scanner.py
# Should show 2 definition lines + 1 usage line; if only 1 line, HIGH-3 not applied.

grep -E '^(streamlit|plotly)' requirements.txt
# Should show both; if empty, HIGH-4 not applied.
```
