# AGORA Handoff

> Operational state for the next agent picking up the build. Updated at the
> end of each keystone. The architectural contract is in
> [`00-FRAMEWORK.md`](00-FRAMEWORK.md); the keystone plan is in
> [`01-KEYSTONE.md`](01-KEYSTONE.md). This file is everything else.

**Last updated after:** Keystone 3 — Trader (merged, PR #15).
**Next up:** Keystone 4 — Reflection (1 calendar week).

---

## TL;DR

Three keystones merged. The platform spawns PMs that paper-trade an Indian
equity simulated venue, persist trades, surface positions on a dashboard,
close at EOD, and respect a kill switch. Zero LLM calls in the platform
yet — that's where K4 starts.

```
K1 — Foundation     ✅  PR #13   docker stack, FastAPI, Postgres, Temporal, dashboard, AgoraLLM client
K2 — Heartbeat      ✅  PR #14   spawnable PM, lifecycle, signals, WS event stream, crash recovery
K3 — Trader         ✅  PR #15   NautilusTrader, paper_trades, trading cycle, EOD, kill switch
K4 — Reflection     ⏭   next     LangGraph build cycle, AgoraLLM in PMs, YAML strategy evolution
K5 — Engineer       ⬜            E2B + OpenHands sandbox engineer agents
K6 — Hierarchy      ⬜            Eng Head + parallel engineer workflows
K7 — Genesis        ⬜            PM2 cold-start + leaderboard + 2-day soak
K8 — Hardening      ⬜            cost caps, auto-merge, recovery drill, polish
```

Plan budget: ~10 calendar weeks of focused work to a 2-PM, 1-engineer-team-each
paper-trading firm. We are roughly 5 weeks in.

---

## Where to start

1. Read this file in full.
2. Read [`01-KEYSTONE.md`](01-KEYSTONE.md) §6 (Keystone 4 — Reflection) for the next keystone's plan.
3. Skim the K3 PR (#15) to absorb the trading layer's shape.
4. Set up your local env and confirm everything boots:
   ```bash
   cd 2.0
   cp .env.example .env       # fill in ANTHROPIC_API_KEY + LANGFUSE_* keys (K4 uses them)
   uv sync --all-groups
   make dashboard-install
   make up
   make db-migrate
   make ci-local              # 200 fast tests
   ```
5. Cut a fresh branch from main: `git switch -c keystone-4/reflection`.

---

## What's actually running

### Stack as of K3

| Component | Where | What it does | Touched in |
|---|---|---|---|
| Postgres + alembic | `infra/docker-compose.yml`, `migrations/` | State of record (PMs, agents, runs, budget, kill switch, paper trades) | K1 → K3 |
| Temporal Server + UI | docker compose, `:7233` and `:8088` | Owns workflow lifecycle (PMSupervisor, EodCloser) | K1 |
| FastAPI control plane | `src/agora/platform/control_plane/`, `:8000` | HTTP API + WebSocket activity stream | K1 → K3 |
| Next.js dashboard | `dashboard/`, `:3000` | One-page live view of PMs, mode, trades, journal, kill switch | K1 → K3 |
| Temporal worker | `src/agora/platform/workers/`, `make worker` | Runs PMSupervisor and EodCloser activities | K1 → K3 |
| AgoraLLM client | `src/agora/platform/llm/` | litellm + Langfuse Cloud + per-call budget recording | K1 (dormant; K4 wires it) |
| NautilusTrader engine | `src/agora/apps/propfirm/trading/` | Paper venue, instrument factory, backtest harness | K3 |
| Market data adapter | `src/agora/apps/propfirm/data/nse.py` | Reads daily bars from `<repo>/stocks/<SYM>/price_history.parquet` | K3 |
| Seed strategy | `src/agora/apps/propfirm/seed_strategies/` | momentum_v1.py (NT) + signals.py (pure-Python, used by cycle) | K3 |

### What you can do today

```bash
make up              # services
make db-migrate      # schema
make api &           # FastAPI on :8000
make worker &        # Temporal worker on agora task queue
make dashboard &     # Next.js on :3000

# Spawn a PM via curl
curl -X POST localhost:8000/api/pms/spawn \
  -H 'content-type: application/json' \
  -d '{"name":"PM1","starting_capital_inr":1000000}'

# Mode is build by default off-hours; for the trading cycle to fire,
# either wait for NSE hours (09:15-15:30 IST) or insert a mode override:
psql -h localhost -U agora -d agora -c \
  "INSERT INTO mode_overrides (mode, expires_at, reason) \
   VALUES ('trading', now() + interval '1 hour', 'manual smoke')"

# Watch journal lines accumulate:
tail -f 2.0/pms/pm1/journals/$(date +%Y-%m-%d).md

# Stop / pause / resume via API
curl -X POST localhost:8000/api/pms/pm1/{stop,pause,resume}

# Kill switch
curl -X POST localhost:8000/api/kill-switch/activate \
  -H 'content-type: application/json' \
  -d '{"reason":"smoke test"}'
curl -X POST localhost:8000/api/kill-switch/deactivate
```

The dashboard at http://localhost:3000 shows everything live.

---

## Conventions established

These are the patterns that worked across K1–K3. New keystones inherit them.

### Branching and PRs

- **One branch per keystone.** Name: `keystone-N/<slug>` (e.g. `keystone-3/trader`).
- **One commit per step** with a tight message: `keystone-N/N.M: <short description>` followed by what shipped and a "drift from plan" callout if any.
- **Audit between implementation and PR.** After all step commits land, the orchestrator runs a read-only audit (delegated to repo-explorer) that grades the work against the plan + axioms and produces optimization findings. Audit fixes ship as `post-audit/N-X` commits on a separate branch fast-forwarded into the PR branch before opening.
- **CI must pass on the PR before merge.** The workflow file at `.github/workflows/agora-ci.yml` is path-scoped to `2.0/**`. (See "Gotchas" below for the YAML float footgun.)
- **Squash or rebase merge.** Per-step history is meaningful; pick whichever shape you want on main. Don't merge-commit.

### Audit checklist (do this before opening every PR)

1. **Plan vs reality** — every "Components built" item from the keystone graded HOLDS / ACCEPTABLE / DRIFT, every "Components NOT built" confirmed not leaked, every DoD item ✅ / ⚠ / ❌ with file:line evidence.
2. **Architectural axioms** — the six principles in `00-FRAMEWORK.md` §4. For each: status (applies / deferred / violated), evidence, concern for next keystones.
3. **Optimization findings** — ranked by impact. What / cost today / fix shape / blocks-next-keystone yes-no.
4. **Next-keystone readiness** — what in the just-shipped work would compound badly when the next keystone lands.
5. **What I would do differently if redoing this keystone.**

The K1, K2, K3 audits each found 4–7 real issues. The pattern works; keep it.

### Sandbox rule (non-negotiable)

**Workflow modules must NOT transitively import network code.** Temporal validates
workflow definitions by re-importing the module under a strict sandbox that
forbids `urllib`, `http.client`, `asyncpg`, `nautilus_trader`, `pandas`, `litellm`,
`sqlalchemy`, etc. Activity bodies run *outside* the sandbox; defer all heavy
imports inside activity functions.

Verify after every change to `pm_supervisor.py` (or any other workflow module):

```bash
uv run python -c "import sys; from agora.platform.workers.pm_supervisor import PMSupervisor; bad=[m for m in sys.modules if any(x in m for x in ['nautilus','litellm','sqlalchemy','asyncpg','pandas','httpx'])]; assert not bad, bad; print('sandbox clean')"
```

Should print `sandbox clean`. If it doesn't, find the leaking import (usually
an `__init__.py` re-exporting something) and defer it inside the activity body.

### Test strategy

- Default `make test` runs unit tests in <10s. **No external services.** Mock
  pools, mock workflows, mock NautilusTrader.
- `make test-all` runs integration too: testcontainers Postgres, Temporal
  `WorkflowEnvironment`, real subprocess workers (the K2 crash recovery
  test). ~90s.
- Tests touching real external services use `@pytest.mark.integration`. Slow
  tests use `@pytest.mark.slow`. Both are deselected from `make test` by
  default; both run in `make test-all`.
- The K2 crash recovery test (`tests/test_pm_crash_recovery.py`) is the most
  important test in the repo. It SIGKILLs a real worker subprocess and
  verifies the workflow resumes after restart. **Don't break it.**

### Status / outcome vocabularies

These are Python `Literal` types, not DB CHECK constraints. K8 hardening
will add the constraints.

```python
# pm_repo.Status
"provisioning" | "spawned" | "running" | "paused" | "stopped" | "error"

# trade_repo.TradeOutcome
"open" | "sl_hit" | "target_hit" | "eod_close" | "manual" | "signal_exit"

# trade_repo.TradeSide
"LONG" | "SHORT"   # K3 strategy is long-only; SHORT included for K4+

# Mode (mode.py)
"build" | "trading" | "pre_trade_freeze"
```

### Source-of-truth decisions (worth knowing)

- **Mode controller** = clock + NSE 2026 holidays + `mode_overrides` table.
  Function lives in `mode.py`; loader in `mode_loader.py`. Don't bypass.
- **PM config** = `<workspace>/config.yaml`, NOT the `pms.config` JSONB
  column. K2 leaves the column at `{}`; K4+ should read and write the YAML.
  Column slated for K8 removal. See `apps/propfirm/README.md` § "PM config:
  source of truth".
- **Trade ledger** = Postgres `paper_trades` table (alembic 0003). Indexed
  on `pm_id` and `(pm_id, outcome)` for the K7 leaderboard query.
- **Workspace tree** = `2.0/pms/<pm_id>/{plans,journals,strategies,research,code}/`.
  `provision_workspace` is idempotent (`mkdir -p` + write-if-absent) so
  Temporal's at-least-once activity replays don't clobber agent state.
- **Journal format** = `[<iso ts>] [<channel>]: <verb> <details>`. One file
  per PM per UTC date at `pms/<pm_id>/journals/<YYYY-MM-DD>.md`. Append-only.

### Workflow / activity / sandbox split (the K2 + K3 lesson)

```
Workflow module top              | Activity body
---------------------------------|-------------------------------------
- __future__                     | - any import
- stdlib (datetime, dataclasses) | - asyncpg, sqlalchemy
- temporalio.{activity,workflow} | - pandas, numpy, nautilus_trader
                                 | - httpx, litellm, langfuse
                                 | - any AGORA module
                                 |
Determinism rules apply:         | Free to call wall-clock, RNG, IO.
- workflow.sleep, not asyncio    | The activity is the impure boundary.
- workflow.now(), not datetime   |
- no DB calls, no file IO        |
- no random, no time.time()      |
```

Patterns that follow this:
- `workers/_pool.py` — process-lifetime asyncpg pool (lazy singleton)
- `workers/_http.py` — process-lifetime httpx client (same shape)
- `workers/_market_data.py` — process-lifetime ParquetMarketData (K3 audit fix)
- `tests/_e2e_workflow_module.py` — pattern for tests that need workflow + heavy imports together

When K4 adds a build cycle activity that calls AgoraLLM, follow the same
pattern: defer the `from agora.platform.llm.client import AgoraLLM` inside
the activity body, NOT at the workflow module top.

---

## Gotchas (real bugs we hit)

### CI YAML float coercion

`working-directory: 2.0` is parsed as a YAML float (becomes `"2"`), and the
runner can't find the directory. Always quote: `working-directory: "2.0"`.
Lesson learned on K2's first PR; fixed in commit `1e23ff6`.

### Workflow re-import under sandbox

K1's `_e2e_workflow_module.py` and K2's `pm_supervisor.py` both hit this.
The Temporal sandbox re-imports the workflow module to validate it; any
transitive `urllib`/`http.client` import fails with
`RestrictedWorkflowAccessError`. The fix is always the same: split the
workflow definition into a module with **only** stdlib + temporalio at
the top, push everything else into activity bodies.

### NautilusTrader venue id can't have hyphens

`Venue("NSE-PAPER")` blew up at engine boot:
`id.value of NSE-PAPER was not equal to account_id.get_issuer() of NSE`.
NautilusTrader splits the venue id on `-` to derive the issuer. Use
`NSEPAPER` (no hyphen). Caught in K3.1.

### `BarDataWrangler` validates OHLCV invariants

It rejects rows where `low > open` or `low > close` or similar. Test
fixtures must enforce `high = max(open, close) + spread` and
`low = min(open, close) - spread` explicitly. Caught in K3.2.

### `StrategyConfig` requires `frozen=True`

NautilusTrader's `StrategyConfig` subclasses must be `frozen=True` or the
engine raises an obscure error. Trivial fix; gnarly to debug.

### `import litellm` is slow

~21 seconds on cold venv. Default test suite was 22s in K2 because every
test triggered the import; K3's stub-heavy unit tests dropped this to 4-5s
because warm caches hit. If `make test` ever balloons back to 20+ seconds,
check whether new tests are pulling litellm transitively.

### `git add -A` will pull in unrelated files

The repo root has untracked files outside `2.0/` (`.kiro/`, `.claude/`,
`AGENTS.md`, `docs/v2/`, etc.) that predate the AGORA work. Always stage
explicit paths: `git add 2.0/path/to/file`. If you accidentally commit
those files, `git reset --soft HEAD~1` and re-stage.

### `git push` before `git commit` (parallel race)

I (the previous orchestrator) did this twice — issued a push call before
the commit landed because they were in the same tool-call batch. Verify
`git log --oneline -3` after each commit; if push reports "Everything
up-to-date" but you expected new commits, run push again.

---

## Lingering items tracked across keystones

These are real but deferred. None block the next keystone; flag them when
the matching keystone lands.

### Tracked for K4 (Reflection)

| Item | Origin | Where to fix |
|---|---|---|
| Strategy load path is hard-coded to `momentum_v1`. K4 needs a YAML loader. | K3 audit Q4 | `cycle.py:265,272`; new module `apps/propfirm/strategy/loader.py` |
| Trade events not on the WebSocket bus. Dashboard sees journal-only. | K3 audit Q3 #5 | `cycle.py:_journal_*` helpers; mirror what `heartbeat_journal` does |
| Build-mode placeholder still calls `heartbeat_journal`. | K3 audit Q4 | `pm_supervisor.py:455-475` else branch |
| Per-PM tools (read_journal, write_strategy, memory_*) need scope checks. | K2 audit | New `platform/tools/` modules; K4 §6 plan §6 covers it |
| Letta + Qdrant containers are up but unused. | K1 → K3 | K4 wires the per-PM Letta agent |

### Tracked for K5 (Engineer)

| Item | Origin | Where to fix |
|---|---|---|
| `INTERNAL_EVENT_TOKEN` model is plaintext compare. Per-PM tokens needed before sandboxed engineers ship. | K2 audit | `app.py:466-469` (move to `secrets.compare_digest`); per-PM rotation |
| `update_pm_status` silent on 0 rows. Matters when K3+ adds delete path. | K2 audit | `pm_repo.py:148-151` |
| `platform/shared/journal.py` extraction (done in K3). Path-scoped wrappers need to land. | K2 audit | New `platform/tools/journal.py` |

### Tracked for K8 (Hardening)

| Item | Origin | Where to fix |
|---|---|---|
| `pms.config` JSONB column is unused; YAML is source of truth. Drop the column. | K3 audit Q4 | New alembic migration |
| `paper_trades.outcome` is plain TEXT. Add a CHECK constraint once vocabulary is frozen. | K3.4 | New alembic migration |
| Dashboard kill-switch reason uses `window.prompt()`. Replace with proper modal. | K3.7 | `dashboard-overview.tsx` |
| `pytest-asyncio` deprecation warnings on Python 3.14. | K1 | Bump pytest-asyncio when a 3.14-clean release lands |
| Holiday list verification against official NSE 2026 PDF before live trading. | K1.5 | `mode.py:NSE_2026_HOLIDAYS` (TODO comment in source) |
| Production deployment automation. K1–K8 are local-first. | K1 plan | New `infra/` deployment scripts; new EC2 sizing |

### Tracked, not blocking

- The K3 trading cycle bypasses NautilusTrader's `Strategy` class (uses
  pure-Python `signals.py` instead). The drift is documented; K4 should
  decide whether to converge on `signals.py` (recommended) or wire NT into
  the live cycle. See K3 audit "What I would do differently" #6.
- `max_positions` config field is declared but unenforced. Single-instrument
  strategy makes it moot for K3. Multi-instrument allocator is K4+ work.
- The K3 daily-bar drift (plan said 1-minute). Strategy and data are
  consistent; documented in `apps/propfirm/README.md`. Intraday data needs
  a paid feed or yfinance fetches — out of K3 scope.

---

## Architectural locks (don't break these)

These are properties verified by tests. Regressing one is a stop-the-line
event.

| Property | Test | Established |
|---|---|---|
| Workflow sandbox stays clean (no nautilus_trader, asyncpg, etc. at module top) | manual sys.modules check (see "Sandbox rule" above) | K2; reverified K3 |
| Crash recovery: SIGKILL worker mid-workflow, fresh worker resumes from history | `tests/test_pm_crash_recovery.py` (integration+slow) | K2 |
| Alembic upgrade/downgrade round-trip succeeds | `tests/test_alembic_roundtrip.py` (slow) | K1 |
| `/api/health` is non-fatal: services down at startup return `down`, not crash | lifespan in `state.py` + `tests/test_health.py` | K1 post-audit |
| Heartbeat / trading-cycle activities have `maximum_attempts=1` retry policy | `tests/test_heartbeat_retry_policy.py`, `pm_supervisor.py` | K2 post-audit, K3.5 |
| Kill switch activate-then-trade race resolves within 1s | `tests/test_kill_switch_cache.py` | K3.7 |

---

## Operational notes

### Local stack RAM

K3 running locally uses ~3 GB:
- Postgres: ~500 MB
- Temporal Server + UI: ~1 GB
- Qdrant: ~300 MB
- Letta: ~1 GB
- FastAPI + worker + dashboard: ~500 MB

K4 will add nothing infrastructural (LangGraph is in-process). K5 will add
E2B sandboxes (which are remote, not local). K8 will add a real deployment
target — likely a fresh EC2 instance, since the legacy `m7i-flex.large`
(8 GB) running the trading-framework predecessor cannot host AGORA.

### What's in `.env`

| Var | Used by | When required |
|---|---|---|
| `ANTHROPIC_API_KEY` | AgoraLLM (K1, dormant; K4 wires it) | K4 |
| `OPENAI_API_KEY` | AgoraLLM fallback / embeddings | K4+ |
| `LANGFUSE_PUBLIC_KEY` + `LANGFUSE_SECRET_KEY` | LLM call tracing | K4+ |
| `LANGFUSE_HOST` | defaults to `https://cloud.langfuse.com` | K4+ |
| `INTERNAL_EVENT_TOKEN` | worker → API publish hook for `pm.heartbeat` events | K2; required for dashboard ticker to show live heartbeats |
| `POSTGRES_URL` | everything | always |
| `TEMPORAL_HOST`, `TEMPORAL_NAMESPACE` | workers + control plane | always |
| `WORKSPACE_ROOT` | overrides default `<repo>/2.0/pms/` | optional |
| `AGORA_STOCKS_ROOT` | overrides default `<repo>/stocks/` | optional |

### Dashboard manual checklist

Before declaring a keystone done, do an operator drill:

1. `make up && make db-migrate`
2. `make api`, `make worker`, `make dashboard` in three terminals
3. Spawn a PM via curl
4. Open `http://localhost:3000` — verify health pills, mode, PM list
5. Click into the PM detail page — verify status badge, journal, controls
6. Test pause / resume / stop — verify status updates within 5s
7. Test kill switch toggle — verify pill flips and trades reject
8. Check `localhost:8088` (Temporal UI) — verify workflow is running

K1 → K3 each shipped this drill before merge. K4+ should too.

### Known wallclock costs

| Operation | Cost |
|---|---|
| `make ci-local` (default) | 4-5s |
| `make test-all` | ~90s |
| `make trading-smoke` | <1s |
| `make momentum-backtest` | <2s |
| Cold `import litellm` | ~21s |
| `make up` to all-healthy | ~25s |
| Crash recovery test (SIGKILL + restart) | ~65s real wallclock |

---

## Communication patterns that worked

These are about how the *previous orchestrator* worked, not about what to
build. Skip if you don't care.

- **Pre-recon for risky external deps.** K3 started with a 30-min
  NautilusTrader smoke (canonical EMACross example) before delegating any
  real work. Caught the `Venue("NSE-PAPER")` hyphen bug, the `frozen=True`
  requirement, and confirmed Python 3.14 compatibility — all before any
  K3 step commit. ~30 minutes of recon saved hours of debugging.
- **Delegate exploration and bulk edits; orchestrate decisions.**
  `repo-explorer` for audits, `code-worker` for multi-file step
  implementation. The orchestrator does planning, decisions, review, and
  the final smoke. Steering doc `05-orchestrator-mode.md` codifies this.
- **One step = one delegation when possible, with verification budget.**
  K3 tightened delegations vs K2 (which combined 2.4 + 2.5 in one batch)
  because the NautilusTrader API translation deserved per-step review.
  Steps 3.1+3.2, 3.3+3.4, 3.6+3.7 were paired naturally; 3.5 (the sandbox
  integration) and 3.6 separately got dedicated delegations.
- **Audit between steps and PR.** Every keystone shipped with 4-7
  audit-driven cleanup commits. The audit is read-only and produces a
  digest; the cleanup is mechanical. The pattern caught real issues every
  time (per-request resource leaks in K1, retry-policy footguns in K2,
  source-of-truth drift in K3).
- **Honest drift reporting.** When the plan said "1-minute bars" and we
  shipped daily, that landed in commit messages, the README, and the PR
  body. When the plan's pseudocode for K3.5 didn't fit (NT strategy ≠
  per-cycle compute), the drift was documented at the source.

---

## Pointers for K4

The next keystone is "Reflection." Plan §6.

The big shape change: the PM gets an LLM brain. Until now PMs are deterministic;
K4 makes them *reason* about their own performance, evolve their plans, and
commit new strategy YAML versions.

What that means concretely:

1. **The build-mode placeholder gets replaced.** Today `pm_supervisor.py`'s
   else branch calls `heartbeat_journal`. K4 swaps in a `build_cycle_activity`
   that runs a LangGraph supervisor agent.
2. **`AgoraLLM` finally gets called.** K1 shipped the wrapper (`platform/llm/client.py`);
   K4 actually invokes it from the PM agent.
3. **Strategy YAML loader.** Replaces the hard-coded `momentum_v1` strategy
   ID in `cycle.py:265,272` with a per-PM `strategies/v001.yaml` reader.
4. **Letta integration.** The per-PM Letta agent persists identity across
   restarts. The `letta_host` env var is set; the container is up; nothing
   reads from it yet.
5. **Tool registry for the PM.** Path-scoped tools the LLM can call:
   `read_journal`, `write_plan`, `write_strategy`, `read_rival_journal`,
   `memory_store`, `memory_search`. Plan §6 Step 4.3 covers it.

The K4 plan estimates 1 calendar week. The riskiest piece is probably the
LangGraph supervisor agent — it's the first time we wire LangGraph into
AGORA and the dependency is heavier than NautilusTrader. Pre-recon
(install LangGraph, run a minimal supervisor example) before delegating
the implementation is the right call.

K3's audit Q4 has the full list of K4 readiness concerns; read it first
(in the PR #15 conversation).

---

## When in doubt

- Plan disagrees with reality? → flag in commit message, document in
  README, surface to the human in the PR body. Don't silently align.
- Tests fail in a confusing way? → re-run the sandbox check first. The
  K2 sandbox property is the #1 source of mysterious Temporal errors.
- Sub-agent comes back with something dubious? → spot-check the
  load-bearing change before believing it. The orchestrator's job
  includes review, not just delegation.
- Stuck for >30 minutes on an external library API? → STOP. Document
  what's confusing, surface to the human. The plan budgets this kind of
  blocker (K3 budgeted a full week for NautilusTrader integration);
  spending another hour fighting docs alone usually doesn't help.
