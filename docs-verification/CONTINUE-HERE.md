# 👋 Handoff — Continue Here

> **If you are an AI/LLM agent picking up this work in a new session, read this file first. It contains everything you need to continue.**

---

## TL;DR for the user

To resume work in a new session, paste this into the chat:

```
Read /Users/gaurav/litellm-bedrock/trading-framework/docs-verification/CONTINUE-HERE.md
and continue the implementation from where it was left off.
```

That's it. The rest of this file is the briefing that the new agent will follow.

---

## Briefing for the next agent

You are continuing implementation work on the **Autonomous Trading Framework** — an
agent-orchestrated equity trading system for the Indian (NSE) market. A previous
session landed a large batch of verification-driven fixes. Your job is to pick up
from there.

### 1. Project location & branch

```bash
cd /Users/gaurav/litellm-bedrock/trading-framework
git status                       # confirm you're on fix/verification-findings
git log --oneline gaurav..HEAD   # if HEAD == gaurav, no commits yet — work is uncommitted
```

The repo lives on branch `fix/verification-findings` (off `gaurav`).
The work may be uncommitted — `git status` will show ~30+ modified/added files.
**Do not commit unless the user explicitly asks.**

### 2. What was done in the previous session

Read these in order:

1. **`docs-verification/STATUS.md`** ← the canonical "what's done / what's pending" tracker.
   Updated at the end of each wave. Read this **first**.
2. **`docs-verification/findings.md`** — the original 10 findings (CRIT-1 → LOW-10), all shipped.
3. **`docs-verification/wave-a-log.md`** — quick wins (6 items, ~1 h).
4. **`docs-verification/wave-b-log.md`** — medium items (9 of 10, ~3.5 h).
5. **`docs-verification/wave-c-log.md`** — larger items (3 of 4, ~1 h; C.4 deferred).
6. **`docs-verification/implementation-log.md`** — chronological log from the very first wave.

Headline numbers:
- **93 unit tests** passing (`python -m pytest`)
- **24 fixes shipped** across the original 10 findings + Waves A/B/C/D
- **0 regressions**, all originally-broken paths now work
- **Live mode is functional** (`config.yaml: trading.mode = live`) — see C.3

### 3. What is still pending

From `docs-verification/STATUS.md` "🔜 Outstanding" section:

**User-only / blocking decisions** (you can't do these unilaterally):

| ID    | Why it's blocked                                                  |
|-------|-------------------------------------------------------------------|
| A1    | Rotate live `.env` credentials — user must do this in person.    |
| E4    | Rewriting git history to remove `paper_trades.db` — destructive. |

**Quick to land if user wants them**:

| ID    | What                                                       | Effort |
|-------|------------------------------------------------------------|--------|
| B.7 / B8 | BSE scrip-code lookup — needs offline scrip-code → symbol table | 1–2 h once data sourced |

**Strategic / multi-day**:

| ID                         | What                                           | Effort |
|----------------------------|------------------------------------------------|--------|
| C.4                         | Backtester consolidation (3+ scripts → 1)     | 2 d    |
| B9                          | Earnings PDF parsing + consensus comparison   | 2–3 d  |
| P2 §17 — §25                | PDF parsing, stock-specific regime, P&L attribution, shadow mode, DuckDB, replay harness | each ~1–3 d |
| P3                          | Multi-broker, options, sector rotation, RL    | research-grade |

**Before doing any P2/P3 work, ask the user**:
- Which item(s) they want
- Whether they want full TDD (which is what was done so far) or faster pragmatic work
- For C.4 (backtester consolidation): they previously deferred this because re-running backtests will move the headline numbers slightly (slippage 0.001 → 0.0005 in `backtest_intraday.py` and `backtest_gap.py`). Confirm they're OK with that before starting.

### 4. Working conventions established by the previous session

You should follow these unless the user says otherwise:

#### a. Test-driven development (red → green → refactor)
Every functional change has a failing test added first, then the implementation, then a
green-suite verification. Tests live in `tests/test_<id>_<short_desc>.py` with one file
per fix (e.g. `test_crit2_signals_persistence.py`). The pytest infra is already set up
(`pytest.ini`, `tests/conftest.py`).

#### b. Run the full suite after every fix
```bash
source .venv/bin/activate
python -m pytest -q
```
Target: stay at green. If a test starts failing, **stop and investigate before moving on**.
This is non-negotiable per `verification-before-completion`.

#### c. Update the implementation log on each item
Append to the right wave file (e.g. `docs-verification/wave-c-log.md`) with what landed,
which tests, and any caveats. Then update `STATUS.md` so the next session sees current state.

#### d. Update project docs alongside code
After landing a fix, flip the corresponding entry in `docs/analysis/05-issues.md` from
"🟠 …" to "✅ RESOLVED — Fixed in `<branch>` (<id>). What landed: …". Same for
`03-agents.md`, `04-decision-pipeline.md`, `technical-reference.md`, `user-guide.md`,
`06-improvements.md` if they reference the issue.

#### e. Don't mutate `config.yaml` from code
The daemon treats `config.yaml` as read-only. Dynamic state goes to
`data/dynamic_watchlist.json` (handled by `core/watchlist.py`). DB schema changes
go through `migrate_trades_schema()` in `agents/execution_agent.py`.

#### f. Use the helpers that now exist
- `core/row_utils.row_get` for sqlite3.Row reads
- `core/costs` for slippage/brokerage constants
- `core/symbols` for NIFTY 50 and ticker normalisation
- `core/timing.timed_run` (auto-applied to every Agent.run already)
- `core/retry.retry` / `with_retry` for network calls
- `core/concurrency.map_symbols` for symbol fan-out
- `core/holidays` for NSE holiday-aware date logic
- `core/config.get_config` for runtime config (cached singleton)
- `core/watchlist.resolve_watchlist` for the effective watchlist

Don't reinvent any of these.

#### g. Skip explicitly-user-only items
A1 (rotate `.env`), E4 (rewrite git history), and any decision that has tradeoffs the
user hasn't approved (e.g. C.4 backtester rewrite) — flag and wait for confirmation.

#### h. ETA updates in chat
The user wants periodic ETA updates as work progresses. Be terse — the docs are the
source of truth; the chat is for status pings only.

### 5. How to verify the current state before starting new work

```bash
cd /Users/gaurav/litellm-bedrock/trading-framework
source .venv/bin/activate

# 1. Suite must be green.
python -m pytest -q
# Expect: "78 passed" (will grow with each new fix you land).

# 2. Verify the migrated DB is healthy.
python -c "
from agents.execution_agent import migrate_trades_schema, get_open_position_symbols, today_pnl_pct
import sqlite3
migrate_trades_schema('paper_trades.db')
conn = sqlite3.connect('paper_trades.db')
print('columns:', [r[1] for r in conn.execute('PRAGMA table_info(trades)').fetchall()])
print('open positions:', get_open_position_symbols('paper_trades.db'))
print('today_pnl_pct:', today_pnl_pct(10000, 'paper_trades.db'))
"
# Expect: 20 columns including weights_applied, technical_score, etc.

# 3. Confirm none of the verification-finding regressions returned.
grep -rn 'config\["watchlist"\]' core/scheduler.py main.py 2>/dev/null   # → empty
grep -rn 'SLIPPAGE\s*=\s*0.001' --include='*.py' . 2>/dev/null | grep -v .venv | grep -v tests/   # → empty
```

### 6. Project geography (in case you need it)

Read these for orientation if you don't have full context:

- **`README.md`** (root) — install + 60-second start.
- **`docs/user-guide.md`** — install/configure/run.
- **`docs/technical-reference.md`** — module APIs, schemas, ops.
- **`docs/analysis/01-architecture.md`** — opinionated architecture overview.
- **`docs/analysis/02-data-flow.md`** — Mermaid flowcharts.
- **`docs/analysis/03-agents.md`** — per-agent deep dive.
- **`docs/analysis/04-decision-pipeline.md`** — line-by-line decision flow.
- **`docs/analysis/05-issues.md`** — current state of issues (✅ ones are done).
- **`docs/analysis/06-improvements.md`** — prioritised roadmap.
- **`graphify-out/GRAPH_REPORT.md`** — auto-generated knowledge graph (run `graphify update .` if stale).

### 7. First message to the user

When you start, send a short message confirming you've read this file. Something like:

> "Read the handoff. Current state: branch `fix/verification-findings`, 78 tests passing,
> 22 fixes shipped in 3 waves. Pending items: <short list from STATUS.md §🔜>. Which one
> would you like me to tackle next?"

Then wait for their answer. Do NOT start work without their go-ahead — the previous
session deliberately stopped here and asked for direction.

### 8. Things that will go wrong if you're not careful

1. **`transformers` is in `requirements.txt` but may not be installed in `.venv`.**
   `import agents.master` indirectly imports `ripple.sentiment_analyzer` which imports
   `transformers`. Some tests work around this with a `_stub_heavy_imports` autouse
   fixture — copy that pattern when writing new tests that import master.

2. **The `Agent` base class auto-wraps every subclass `run` in `timed_run`.**
   When you read agent code and notice timing log lines you didn't add, that's why.
   Set `_TIMED_RUN = False` on a subclass to opt out (none currently do).

3. **`STOCKS_DIR` is module-level in `core/knowledge_base.py`, not cwd-relative.**
   When testing fixtures that involve per-stock JSON, `monkeypatch.setattr("core.knowledge_base.STOCKS_DIR", tmp_path)`. `monkeypatch.chdir` alone is not enough.

4. **`paper_trades.db.bak.20260516-173731` is the pre-migration backup.**
   Don't delete it for at least one full trading week.

5. **The user has been clear about saving tokens.** Don't dump long explanations in the
   chat. Update the docs and ping with ETAs only.

### 9. If you're confused or the docs are stale

Run a fresh verification pass:

```bash
graphify update .                            # refresh the auto-generated knowledge graph
python -m pytest -q                          # confirm baseline
git diff --stat gaurav..HEAD                 # see all changes since the docs branch
```

Then re-read `docs-verification/STATUS.md`. If something is unclear, ask the user
before guessing.

---

## Quick reference card

```
Branch:          fix/verification-findings
Repo:            /Users/gaurav/litellm-bedrock/trading-framework
Test command:    python -m pytest -q
Test count:      148 (as of 2026-05-16 after Wave G)
UI entry point:  streamlit run app.py   (3 pages: Setup · How It Works · Dashboard)
DB backup:       paper_trades.db.bak.20260516-173731
Status doc:      docs-verification/STATUS.md
This file:       docs-verification/CONTINUE-HERE.md
Skipped items:   A1 (user), B9 (design), C.4 (large), E4 (skip — keep db in repo), P2/P3
Conventions:     TDD, run full suite per fix, update docs alongside code
Don't:           commit without ask, mutate config.yaml from code, dump in chat
```

Good luck. The previous session left you a clean baseline; build from there.
