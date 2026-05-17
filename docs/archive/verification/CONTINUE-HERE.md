# 👋 Handoff — Continue Here

> **If you are an AI/LLM agent picking up this work in a new session, read this file first. It is the single source of truth for project state, what's done, what's pending, and how to continue.**

---

## TL;DR for the human

Paste this into the new agent's chat:

```
Read /Users/gaurav/litellm-bedrock/trading-framework/docs-verification/CONTINUE-HERE.md
and then read docs-verification/CODE-REVIEW.md.
Then propose what to tackle first and wait for my go-ahead.
```

The rest of this file is the briefing.

---

## Briefing for the next agent

You are continuing work on the **Autonomous Trading Framework** — an agent-orchestrated equity
trading system for the Indian (NSE) market. Two prior implementation passes have landed a
substantial codebase. Your job is to pick up cleanly.

### 1. Project location & branch

```bash
cd /Users/gaurav/litellm-bedrock/trading-framework
git branch --show-current   # currently: feat/bloomberg-ui
git status                  # likely clean (work is committed)
git log --oneline -5
```

The active branch is **`feat/bloomberg-ui`**. The earlier branch
`fix/verification-findings` was merged into it. Both still exist locally.

### 2. What was done before you arrived

Two waves of implementation:

**Wave 1 — verification findings + quick wins + medium + larger items**
(branch `fix/verification-findings`, then merged):

- 22 fixes across 3 internal sub-waves (CRIT-1, CRIT-2, HIGH-3..5, MED-6..8, LOW-9, LOW-10, plus B7, B13, B14, B.1..B.10, C.1..C.3, etc.)
- 78 unit tests added.
- Full per-fix write-up in `docs-verification/STATUS.md`, `wave-a-log.md`,
  `wave-b-log.md`, `wave-c-log.md`, `implementation-log.md`.

**Wave 2 — strategic / P2 / P3 + UI** (post-merge on `feat/bloomberg-ui`):

The user (or a previous agent) extended the implementation to cover items that the first
wave had marked deferred or strategic. Highlights:

| Area                                  | What landed                                                              |
|---------------------------------------|--------------------------------------------------------------------------|
| BSE scrip-code lookup (B.7)            | `core/bse_scrip.py` + 4868-row bundled CSV                               |
| Backtester consolidation (C.4)         | `core/backtester.py` — `Strategy` ABC + `GapStrategy` + `IntradayMLStrategy` |
| EPS consensus parsing (B9)             | `agents/earnings_calendar_agent.py:fetch_eps_consensus`                  |
| Stock-specific regime (P2 §18)         | `agents/regime_agent.py:compute_stock_regime` + `blend_regimes`          |
| Per-signal P&L attribution (P2 §19)   | `agents/execution_agent.py:signal_attribution` + `signal_source` column  |
| Shadow mode (P2 §21)                   | `core/broker.py:ShadowBroker`                                            |
| DuckDB store (P2 §22)                  | `core/duckdb_store.py`                                                   |
| ML promotion gate (P2 §23)             | `_save_if_better` + AUC tracking in `ml_model.py` and `india_intraday_model.py` |
| Anomaly alerts (P2 §24)                | `core/scheduler.py` (0-result pre-open + 75% P&L thresholds)             |
| Replay harness (P2 §25)                | `core/replay.py`                                                         |
| Multi-broker stubs (P3)                | `UpstoxBroker`, `AngelOneBroker` in `core/broker.py`                     |
| Sector rotation (P3)                   | `agents/sector_rotation_agent.py`                                        |
| Bloomberg-level UI                     | `ui/app.py` + `ui/pages/{1_Setup,2_How_It_Works,3_Dashboard}.py`         |
| Onboarding docs                        | `onboarding/WHAT_YOU_NEED.md`, `setup/README.md`                         |
| UI plan                                | `ui-plan/BLOOMBERG_UI_PLAN.md`                                           |

Test count: **175 unit tests, all green.**

### 3. Current code-review status

All findings from `CODE-REVIEW.md` resolved in Wave H (2026-05-17):

- ✅ B-1, M-1..M-5, N-1..N-7 — all fixed (see `STATUS.md` Wave H table).
- ✅ Backtest re-run complete — headline numbers in `docs-verification/backtest-results-post-b1.md`.

What's left that requires you or external dependencies:
- **A1** — rotate live `.env` credentials
- **Real broker impls** — Upstox + Angel One (need live tokens + SDKs)
- **Full-pipeline replay** — extend `core/replay.py` to plug in arbitrary `Strategy` subclasses

The previous session estimated **~2 h to clean everything up** (B-1 + M-1..M-5) following
TDD per the established conventions.

### 4. What is still genuinely pending (beyond CODE-REVIEW)

**User-only / blocking decisions** (you cannot do these unilaterally):

| ID    | Why it's blocked                                                    |
|-------|--------------------------------------------------------------------|
| A1    | Rotate live `.env` credentials. Only the user can do this.         |
| E4    | Rewriting git history to remove `paper_trades.db` — destructive.   |

**Real implementations to land** (multi-broker stubs are NotImplementedError):

| ID                      | What                                                  | Effort |
|-------------------------|--------------------------------------------------------|--------|
| Upstox real impl        | `core/broker.py:UpstoxBroker` — needs upstox-python-sdk + creds | 1d |
| Angel One real impl     | `core/broker.py:AngelOneBroker` — needs smartapi-python + creds | 1d |
| Replay full pipeline    | `core/replay.py` only runs gap strategy; extend to plug in `Strategy` ABC subclasses | 0.5d |
| Backtester re-run       | After **B-1** lands, re-run all backtests; document new headline numbers | 0.5d |

**Strategic stuff that hasn't been touched**:

- Live trading shake-down (mode=`live` is functional but never run for real)
- Performance / SLO instrumentation (timing logs exist but no Prometheus)
- Test infrastructure for the UI (Streamlit pages have no tests)

### 5. How to verify the current state before doing anything

```bash
cd /Users/gaurav/litellm-bedrock/trading-framework
source .venv/bin/activate

# 1. Suite must be green.
python -m pytest -q
# Expect: "148 passed". If anything fails, STOP and figure out why before changing anything.

# 2. DB schema is the post-CRIT-2 shape.
python -c "
import sqlite3
from agents.execution_agent import migrate_trades_schema
migrate_trades_schema('paper_trades.db')
conn = sqlite3.connect('paper_trades.db')
cols = [r[1] for r in conn.execute('PRAGMA table_info(trades)').fetchall()]
expected = {'technical_score', 'sentiment', 'pattern_ev', 'sector_momentum',
            'regime_alignment', 'weights_applied', 'signal_source'}
missing = expected - set(cols)
print(f'columns: {len(cols)}  missing: {missing or \"none\"}')
"

# 3. Sanity check on the canonical helpers.
python -c "
from core.row_utils import row_get
from core.costs import SLIPPAGE_FRAC, BROKERAGE_FRAC
from core.symbols import NIFTY_50
from core.bse_scrip import scrip_to_symbol
print('SLIPPAGE_FRAC:', SLIPPAGE_FRAC)
print('NIFTY_50 entries:', len(NIFTY_50))
print('500325 →', scrip_to_symbol('500325'))   # should be RELIANCE
"

# 4. UI smoke (optional).
streamlit run ui/app.py --server.headless true & sleep 5; curl -s localhost:8501 | head -5; kill %1
```

### 6. Working conventions you must follow

These are non-negotiable per the established codebase:

| Convention | Why |
|------------|-----|
| **TDD red→green→refactor** | Every existing fix has tests written first. Match the pattern. |
| **Run `python -m pytest -q` after every change** | Stay green. If something breaks, stop and investigate. |
| **One pytest file per fix**, named `tests/test_<id>_<short>.py` | Matches existing layout. |
| **Update `docs-verification/<wave-X>-log.md` and `STATUS.md` per item** | The next session sees current state. |
| **Update `docs/analysis/05-issues.md` and `06-improvements.md`** when an issue is resolved | Project docs must reflect code reality. |
| **Don't mutate `config.yaml` from code** | Dynamic state goes to `data/dynamic_watchlist.json`. |
| **Use existing helpers** — don't reinvent | `core.row_utils`, `core.costs`, `core.symbols`, `core.timing`, `core.retry`, `core.concurrency`, `core.holidays`, `core.config`, `core.watchlist`, `core.bse_scrip`, `core.duckdb_store`. |
| **Don't commit unless the user explicitly asks** | The user controls git history. |
| **Skip user-only items** (A1, E4) — flag and wait | Some items require human-in-the-loop. |
| **Be terse in chat; update the docs** | The user has been clear about saving tokens. |

### 7. First message to send the user

When you start a new session, your first message should be a brief confirmation, e.g.:

> "Read the handoff and the code-review findings. Current state: branch `feat/bloomberg-ui`,
> 148 tests passing, 1 real bug (B-1 in `core/replay.py`) + 5 medium issues + 7 nits flagged
> in `CODE-REVIEW.md`. The cleanest first step is fixing B-1 (~5 min, will rebalance any
> replay reports), then moving down the M-1..M-5 list. Want me to start there, or did you
> have something else in mind?"

Then **wait for their answer**. Do NOT start work without their go-ahead.

### 8. Pitfalls / things that have caught past sessions out

1. **`transformers` may not be installed in the `.venv`** even though it's in
   `requirements.txt`. Tests work around this with a `_stub_heavy_imports` autouse
   fixture. If a new test needs `agents.master`, copy that pattern.

2. **`Agent` base class auto-wraps every subclass `run` in `core.timing.timed_run`.**
   Timing log lines you didn't add appear in test output — that's expected. Set
   `_TIMED_RUN = False` on a subclass to opt out.

3. **`STOCKS_DIR` is module-level, not cwd-relative.** When a test fixture writes
   per-stock JSON, `monkeypatch.setattr("core.knowledge_base.STOCKS_DIR", tmp_path)`.
   `monkeypatch.chdir` alone is not enough.
   Note: `core/duckdb_store.py:STOCKS_DIR` is the EXCEPTION (M-2 in CODE-REVIEW).

4. **`paper_trades.db.bak.20260516-173731`** is the pre-CRIT-2 migration backup.
   Don't delete for at least one full trading week.

5. **Replay reports are biased upward** until B-1 lands. Don't compare new replay output
   to old replay output as a regression check.

6. **`fix/verification-findings` branch still exists locally** but its work is fully
   merged into `feat/bloomberg-ui`. Ignore it.

7. **The user has been clear about not dumping in chat.** Update the docs and ping with
   ETAs only.

### 9. Quick reference card

```
Branch:          feat/bloomberg-ui
Repo:            /Users/gaurav/litellm-bedrock/trading-framework
Test command:    python -m pytest -q
Test count:      175 (as of 2026-05-17 01:05 IST)
Status doc:      docs-verification/STATUS.md
Code review:     docs-verification/CODE-REVIEW.md   ← read this first
This file:       docs-verification/CONTINUE-HERE.md
DB backup:       paper_trades.db.bak.20260516-173731
Skipped items:   A1 (user), E4 (git), real broker impls (live tokens)
Conventions:     TDD, run full suite per fix, update docs alongside code
Don't:           commit without ask, mutate config.yaml from code, dump in chat
```

### 10. If something here is stale

Run a fresh verification pass:

```bash
graphify update .                                  # refresh knowledge graph
python -m pytest -q                                # baseline test count
git log --oneline -10                              # see what changed since this doc
ls docs-verification/                              # see if newer logs exist
```

If `STATUS.md` looks wrong, ask the user before guessing.

---

Good luck. The previous sessions have left you a clean baseline plus a prioritised work
queue. Build from there.
