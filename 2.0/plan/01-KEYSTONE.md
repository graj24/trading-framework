# The Keystone Plan

> Step-by-step implementation plan for AGORA.
> Eight keystones. Each is the wedge that locks the next stage of the arch.
> If a keystone fails, the rest of the plan is not load-bearing.

---

## Table of Contents

1. [How to Read This Plan](#1-how-to-read-this-plan)
2. [Keystones at a Glance](#2-keystones-at-a-glance)
3. [Keystone 1 — Foundation](#3-keystone-1--foundation)
4. [Keystone 2 — Heartbeat](#4-keystone-2--heartbeat)
5. [Keystone 3 — Trader](#5-keystone-3--trader)
6. [Keystone 4 — Reflection](#6-keystone-4--reflection)
7. [Keystone 5 — Engineer](#7-keystone-5--engineer)
8. [Keystone 6 — Hierarchy](#8-keystone-6--hierarchy)
9. [Keystone 7 — Genesis](#9-keystone-7--genesis)
10. [Keystone 8 — Hardening](#10-keystone-8--hardening)
11. [Cross-Cutting Concerns](#11-cross-cutting-concerns)
12. [Anti-Patterns to Avoid](#12-anti-patterns-to-avoid)
13. [Pre-Flight Checklist](#13-pre-flight-checklist)

---

## 1. How to Read This Plan

Each keystone has the same structure:

- **Goal** — one sentence, concrete, observable.
- **Why now** — what would break if we deferred this.
- **Definition of done** — the test you run to know it works. If this test does not pass, the keystone is not complete; do not move on.
- **Components built** — what code/infra ships in this keystone.
- **Components NOT built (deferred)** — explicit, so you do not gold-plate.
- **Detailed steps** — ordered work items. Each step has a verification.
- **Risks and tripwires** — known ways this can go sideways.
- **Estimated time** — calendar weeks of focused solo work.

The estimates assume one human (you), focused, with the understanding that AGORA is the priority project. Halve them at your peril; double them at your prudence.

The plan is **strictly sequential**. Do not start keystone N+1 before keystone N's definition of done is satisfied. The point of a keystone arch is that you cannot put weight on the upper stones until the lower ones are locked. The same applies here.

The repo home for AGORA is `2.0/` in this repository, with documents in `2.0/plan/` and code organized as described in §6 of the framework doc. The existing `trading-framework/` files outside `2.0/` are reference material only, not a runtime dependency.

---

## 2. Keystones at a Glance

| # | Name | Goal in one line | Time |
|---|---|---|---|
| 1 | Foundation | Empty platform skeleton: Postgres, Temporal, FastAPI, Next.js, Langfuse, all up. | 1 week |
| 2 | Heartbeat | A spawnable PM that does nothing but log "I'm alive" in build/trading mode. | 1 week |
| 3 | Trader | PM1 places real (paper) trades through NautilusTrader using a seed strategy. | 2 weeks |
| 4 | Reflection | PM1 reads its own performance, journals, evolves its own strategy YAML. | 1 week |
| 5 | Engineer | PM1 spawns one engineer in E2B. Engineer writes code, opens a PR. | 2 weeks |
| 6 | Hierarchy | PM1 spawns Eng Head; Eng Head spawns multiple engineers; Eng Head triages PRs. | 1 week |
| 7 | Genesis | PM2 cold-starts from scratch. Two PMs compete on the same leaderboard. | 1 week |
| 8 | Hardening | Cost caps, kill switch, auto-merge, observability polish, recovery drills. | 1 week |

**Total: ~10 calendar weeks of focused solo work** for a 2-PM, 1-engineer-team-each prop firm running on a durable, observable platform. Research team (mirror of engineering team) deferred to a post-v1 keystone — same shape as Keystones 5–6 but producing reports instead of PRs.

---

## 3. Keystone 1 — Foundation

### Goal
Stand up every piece of infrastructure AGORA depends on. No agents yet. Just the bones.

### Why now
Every later keystone touches one or more of these services. If we build PMs before the platform is stable, we will be debugging two unstable systems simultaneously.

### Definition of done
- I can run `make up` and the entire stack starts: Postgres, Temporal, Langfuse, FastAPI, Next.js, Letta, Qdrant.
- I can open `http://localhost:3000` and see an empty AGORA dashboard with placeholders for "PMs (0)" and "PRs (0)."
- I can `curl POST /api/health` and get `{"status": "ok", "services": {...}}` listing all services as healthy.
- A throwaway hello-world Temporal workflow runs end-to-end and shows up in Temporal Web UI.
- A throwaway LLM call (litellm to Sonnet) appears as a span in Langfuse.

### Components built
- Repo skeleton at `2.0/` with the directory layout from framework doc §7.9.
- `docker-compose.yml` for local dev: Postgres 16, Temporal Server, Langfuse, Qdrant, Letta server, NATS (event bus, optional but cheap).
- FastAPI app skeleton at `platform/control_plane/`. Health endpoint, CORS, Postgres connection, structured logging.
- Next.js app skeleton at `platform/dashboard/`. One page (`/`) with the empty state.
- Temporal Python worker skeleton at `platform/workers/`. Registers a hello-world workflow.
- litellm wrapper at `platform/llm/` with Langfuse hook installed.
- `pyproject.toml` with all dependencies pinned. uv as the package manager.
- `Makefile` with: `up`, `down`, `logs`, `test`, `lint`, `typecheck`, `db-migrate`, `db-shell`.
- `.env.example` listing every secret name (no values).
- Pre-commit hooks: ruff, black, mypy.
- A single CI workflow at `.github/workflows/ci.yml`: lint, types, unit tests on PR.

### Components NOT built (deferred)
- Any agent code. No PM, no engineer, no LangGraph.
- Any trading logic. No NautilusTrader integration yet.
- Any sandbox provisioning. No E2B yet.
- Any auth. Local-only for now.
- Production deployment. Local-only for now.

### Detailed steps

**Step 1.1 — Repo bootstrap.**

Create the `2.0/` directory structure.

```
2.0/
├── platform/
│   ├── control_plane/
│   ├── workers/
│   ├── tools/
│   ├── memory/
│   ├── llm/
│   ├── observability/
│   ├── dashboard/
│   └── shared/
├── apps/
│   └── propfirm/         (empty, populated in K3)
├── pms/                   (empty)
├── plan/                  (00-FRAMEWORK.md, 01-KEYSTONE.md)
├── tests/
├── ci/
├── infra/
│   └── docker-compose.yml
├── .env.example
├── pyproject.toml
├── Makefile
└── README.md
```

Initialize as a separate Python package (`agora`). Do not import from `trading-framework/`'s top-level packages. The clean break is the point.

**Verification:** `tree -L 2 2.0/` shows the layout above.

**Step 1.2 — docker-compose for infra.**

`infra/docker-compose.yml` with:
- `postgres:16-alpine`, with two databases: `agora` (control plane) and `temporal` (Temporal Server).
- `temporalio/auto-setup:1.27` (Temporal Server, simplest local mode).
- `temporalio/ui:2.40` (Temporal Web UI).
- `qdrant/qdrant:v1.13.0`.
- `letta/letta:0.5.0` (or current stable).
- `langfuse/langfuse:3` plus its Postgres + ClickHouse dependencies.
- A single `agora-net` bridge network.

Document required `.env` keys but let the compose file fail noisily if a secret is missing rather than silently skipping containers.

**Verification:** `docker compose up` brings everything up. `docker compose ps` shows all services healthy. Each service has a brief `make smoke-<service>` script that hits its health endpoint.

**Step 1.3 — Postgres schema (initial).**

Migrations live at `platform/control_plane/migrations/`. Use Alembic.

Initial schema:

```sql
CREATE TABLE pms (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    status          TEXT NOT NULL,
    starting_capital_inr NUMERIC NOT NULL,
    spawned_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    stopped_at      TIMESTAMPTZ,
    prompt_path     TEXT NOT NULL,
    config          JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE agents (
    id              TEXT PRIMARY KEY,
    pm_id           TEXT REFERENCES pms(id) ON DELETE CASCADE,
    parent_agent_id TEXT REFERENCES agents(id) ON DELETE CASCADE,
    role            TEXT NOT NULL,            -- 'pm' | 'eng_head' | 'engineer' | 'research_lead' | 'researcher'
    status          TEXT NOT NULL,            -- 'idle' | 'running' | 'paused' | 'stopped' | 'error'
    started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    config          JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE runs (
    id              BIGSERIAL PRIMARY KEY,
    agent_id        TEXT REFERENCES agents(id),
    started_at      TIMESTAMPTZ NOT NULL,
    ended_at        TIMESTAMPTZ,
    tokens_in       INT,
    tokens_out      INT,
    cost_usd        NUMERIC(10, 6),
    model           TEXT,
    langfuse_trace  TEXT
);

CREATE TABLE budget_events (
    id              BIGSERIAL PRIMARY KEY,
    pm_id           TEXT REFERENCES pms(id),
    ts              TIMESTAMPTZ NOT NULL DEFAULT now(),
    kind            TEXT NOT NULL,            -- 'llm_call' | 'sandbox_min' | 'embedding' | 'manual'
    amount_usd      NUMERIC(10, 6) NOT NULL,
    metadata        JSONB
);

CREATE TABLE kill_switch (
    id              INT PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    active          BOOL NOT NULL DEFAULT FALSE,
    activated_at    TIMESTAMPTZ,
    reason          TEXT
);
INSERT INTO kill_switch (id, active) VALUES (1, FALSE);

CREATE TABLE mode_overrides (
    id              BIGSERIAL PRIMARY KEY,
    requested_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    mode            TEXT NOT NULL,            -- 'build' | 'trading' | 'pre_trade_freeze'
    expires_at      TIMESTAMPTZ NOT NULL,
    reason          TEXT
);
```

Tables for `tasks`, `prs`, `trades`, `journals` come in their respective keystones.

**Verification:** `make db-migrate` runs cleanly. `\dt` in `psql` shows the tables.

**Step 1.4 — FastAPI control plane skeleton.**

`platform/control_plane/app.py`:
- App factory.
- `/api/health` returning service status (Postgres ping, Temporal client ping, Langfuse ping, Letta ping, Qdrant ping).
- `/api/pms` returning empty list.
- `/api/mode` returning mode-controller decision.
- Structured logging with `loguru`. JSON output in production, human-readable in dev.
- Request ID middleware. Every request carries an `X-Request-ID`; logged with every log line.

**Verification:** `make api` starts uvicorn. `curl localhost:8000/api/health` returns the expected JSON.

**Step 1.5 — Mode controller.**

`platform/control_plane/mode.py`:
- `current_mode(now: datetime, calendar: HolidayCalendar, overrides: list[Override]) -> Mode`
- `HolidayCalendar` initially backed by a hard-coded list of NSE holidays for 2026 (defer fetching from NSE to a later keystone).
- A simple loop runs every 30 seconds, computes mode, publishes change events to NATS (or just prints in K1).
- Test cases: weekday at 09:14, 09:15, 13:00, 15:30, 15:31; Saturday; holiday; with active override.

**Verification:** Unit tests in `tests/test_mode_controller.py` cover each boundary case. `make test` passes.

**Step 1.6 — Temporal worker skeleton.**

`platform/workers/main.py`:
- Connects to Temporal at `localhost:7233`.
- Registers one hello-world workflow:

```python
@workflow.defn
class HelloWorkflow:
    @workflow.run
    async def run(self, name: str) -> str:
        result = await workflow.execute_activity(
            say_hello, name, start_to_close_timeout=timedelta(seconds=5)
        )
        return result

@activity.defn
async def say_hello(name: str) -> str:
    return f"hello, {name}"
```

A `tyro`/`click` CLI: `agora-worker run` to start.

**Verification:** `make worker` starts the worker. `agora-cli hello world` triggers the workflow. Result returns. Temporal Web UI shows the workflow.

**Step 1.7 — litellm + Langfuse wrapper.**

`platform/llm/client.py`:

```python
class AgoraLLM:
    def __init__(self, agent_id: str, pm_id: str, langfuse: Langfuse):
        self.agent_id = agent_id
        self.pm_id = pm_id
        self.lf = langfuse

    async def call(self, model: str, messages: list[dict], **kwargs) -> Response:
        with self.lf.trace(name=f"{self.agent_id}.llm_call",
                           metadata={"agent_id": ..., "pm_id": ...}) as trace:
            response = await litellm.acompletion(model=model, messages=messages, **kwargs)
            trace.update(input=messages, output=response.choices[0].message.content,
                         usage=response.usage)
            await self._record_cost(response.usage, model)
            return response

    async def _record_cost(self, usage, model):
        cost = compute_cost(usage, model)
        await db.execute(
            "INSERT INTO budget_events (pm_id, kind, amount_usd, metadata) "
            "VALUES (:pm_id, 'llm_call', :cost, :meta)",
            pm_id=self.pm_id, cost=cost, meta={"model": model, "tokens": usage.total_tokens}
        )
```

Cost computation pulls from `litellm.cost_per_token`.

**Verification:** A standalone smoke script `scripts/smoke_llm.py` does one call and shows up in Langfuse.

**Step 1.8 — Next.js dashboard skeleton.**

`platform/dashboard/`:
- Next.js 15 app router.
- Home page `/` showing "AGORA — 0 PMs running, mode: build, kill switch: off."
- Reads from `/api/health`, `/api/mode`, `/api/pms`.
- Uses `shadcn/ui` for basic components (avoid premature design; this is a tool, not a product).
- TanStack Query for data fetching with auto-refresh every 5s.

**Verification:** `make dashboard` starts dev server on port 3000. Page loads, shows the placeholder.

**Step 1.9 — Test harness + CI.**

- `pyproject.toml` includes pytest, pytest-asyncio, ruff, black, mypy, hypothesis.
- `tests/conftest.py` with fixtures for: ephemeral Postgres (testcontainers), in-memory Temporal (`temporal_test`), mocked litellm.
- One end-to-end test: spawn a hello workflow, assert it completes, assert a budget event was recorded.
- GitHub Actions: install deps, run lint + types + tests on every PR.

**Verification:** `make ci-local` (which runs the same as CI) passes.

**Step 1.10 — README.**

`2.0/README.md` answers:
- What is AGORA? (One paragraph; link to `plan/00-FRAMEWORK.md`.)
- How do I get it running? (Three commands.)
- What is the current state? (Reference current keystone.)

### Risks and tripwires

- **Temporal local setup is finicky.** The `auto-setup` image is the path of least resistance for dev. Production will use a managed Temporal Cloud or self-hosted cluster — that decision is for K8 (Hardening), not now.
- **Langfuse self-hosted has a real footprint.** It needs Postgres + ClickHouse + Redis. If this is too much for your local laptop, run only when actively developing observability features. The control plane should not crash if Langfuse is down.
- **Letta is a moving target.** The API has changed across versions. Pin a specific version in K1 and document the rationale. Plan to upgrade in a dedicated future keystone if needed.
- **Path: do not import old `trading-framework` packages.** This is the clean break. If you find yourself reaching for `from core.event_bus import ...`, copy the file into `2.0/platform/` and adapt it. Do not symlink, do not add to PYTHONPATH.

### Estimated time
**1 calendar week.** Two days on infra setup, two on FastAPI + Postgres + Temporal wiring, one on Next.js + Langfuse + tests.

---

## 4. Keystone 2 — Heartbeat

### Goal
A spawnable PM that does nothing but prove the lifecycle works: start, observe in dashboard, switch modes with the clock, stop, restart and resume.

### Why now
Before the PM does anything useful, we need to know its lifecycle is sound. Tree-rooted lifecycle is the most architecturally important property. If we get it wrong, every later keystone is built on sand.

### Definition of done
- `curl POST /api/pms/spawn -d '{"name": "PM1", ...}'` creates a PM. Returns `pm_id`.
- The dashboard shows PM1 listed, status "running", current mode (build or trading).
- The PM appends a heartbeat to its journal every 60 seconds in build mode and every 60 seconds in trading mode.
- The journal entries differ between modes ("[build]: alive" vs "[trading]: alive").
- `curl POST /api/pms/pm1/stop` stops the PM. Status becomes "stopped." No more heartbeats.
- I can kill the entire AGORA process group, restart `make up`, and PM1 resumes from where it left off (because Temporal owns the workflow).
- I can pause and resume PM1.

### Components built
- `POST /api/pms/spawn`, `POST /api/pms/{id}/stop`, `POST /api/pms/{id}/pause`, `POST /api/pms/{id}/resume`, `GET /api/pms`, `GET /api/pms/{id}`.
- `PMSupervisor` Temporal workflow with: heartbeat activity, mode-aware loop, signal handlers for stop/pause/resume.
- PM workspace provisioning activity: creates `/pms/<pm_id>/{plans,journals,strategies,research,code}/`, initial files.
- A minimal PM "agent" that is just an async function returning text. No LangGraph yet.
- Journal write helpers in `platform/shared/journal.py`.
- Dashboard PM list + PM detail page (showing journal tail and current state).
- Live activity stream over WebSocket from control plane to dashboard.

### Components NOT built (deferred)
- Any LLM calls in the PM. The heartbeat is plain Python.
- Any trading logic. No NautilusTrader yet.
- Memory layer (Letta). PMs do not "remember" anything yet.
- Sub-agents. No engineers, no research.
- Cost tracking beyond infra cost. No LLM = no LLM cost.

### Detailed steps

**Step 2.1 — Spawn endpoint.**

```python
@router.post("/api/pms/spawn")
async def spawn_pm(req: SpawnPMRequest) -> SpawnPMResponse:
    pm_id = req.name.lower().replace(" ", "_")           # 'pm1', 'pm2', ...
    if await pm_exists(pm_id):
        raise HTTPException(409, "PM already exists")

    await provision_workspace(pm_id, req)                # creates /pms/<pm_id>/...
    await db.insert_pm(pm_id, req)

    # Start Temporal workflow
    handle = await temporal_client.start_workflow(
        PMSupervisor.run, pm_id, req.config,
        id=f"pm-{pm_id}",
        task_queue="agora",
        cron_schedule=None
    )
    await db.set_pm_workflow_id(pm_id, handle.workflow_id)
    return SpawnPMResponse(pm_id=pm_id, workflow_id=handle.workflow_id)
```

Workspace provisioning activity creates the directory tree and seeds:
- `/pms/<pm_id>/plans/current.md` with "PM <name> initialized at <ts>."
- `/pms/<pm_id>/journals/<today>.md` empty.
- `/pms/<pm_id>/config.yaml` with defaults.

**Verification:** `curl` the endpoint, see `pm1` appear in `GET /api/pms`, see directory created on disk.

**Step 2.2 — PMSupervisor workflow.**

```python
@workflow.defn
class PMSupervisor:
    def __init__(self):
        self._stopped = False
        self._paused = False

    @workflow.signal
    def stop(self):
        self._stopped = True

    @workflow.signal
    def pause(self):
        self._paused = True

    @workflow.signal
    def resume(self):
        self._paused = False

    @workflow.run
    async def run(self, pm_id: str, config: dict):
        await workflow.execute_activity(
            mark_pm_running, pm_id,
            start_to_close_timeout=timedelta(seconds=10)
        )
        try:
            while not self._stopped:
                if self._paused:
                    await workflow.sleep(timedelta(seconds=10))
                    continue
                mode = await workflow.execute_activity(
                    get_current_mode,
                    start_to_close_timeout=timedelta(seconds=5)
                )
                await workflow.execute_activity(
                    heartbeat, pm_id, mode,
                    start_to_close_timeout=timedelta(seconds=15)
                )
                await workflow.sleep(timedelta(seconds=60))
        finally:
            await workflow.execute_activity(
                mark_pm_stopped, pm_id,
                start_to_close_timeout=timedelta(seconds=10)
            )
```

Activities:
- `mark_pm_running(pm_id)` — set DB status.
- `get_current_mode()` — call mode controller.
- `heartbeat(pm_id, mode)` — append a line to the PM's journal.
- `mark_pm_stopped(pm_id)` — set DB status.

**Verification:** Spawn a PM, watch journal file get appended every 60 seconds. Watch mode entries change as you cross 09:15 IST (or with manual mode override).

**Step 2.3 — Stop, pause, resume signals.**

`POST /api/pms/{id}/stop`:

```python
handle = temporal_client.get_workflow_handle(workflow_id)
await handle.signal(PMSupervisor.stop)
# Workflow exits its loop on next iteration.
```

Pause and resume use `PMSupervisor.pause` / `PMSupervisor.resume` signals.

**Verification:** Stop the PM mid-cycle. Heartbeats stop. Status updates to "stopped." Restart: `make down && make up`. Spawn a new PM with the same name? Should fail (still in DB). Or design as: stopping a PM does not delete it; it is in "stopped" state and can be re-started by re-running the workflow. (Design choice: re-start uses a fresh workflow id; the old one is terminal.)

**Step 2.4 — Dashboard PM detail page.**

`/pms/[id]` route in Next.js. Shows:
- Name, status, current mode.
- Last 50 journal entries (read from filesystem via `/api/pms/{id}/journal?lines=50`).
- "Stop" / "Pause" / "Resume" buttons.

Auto-refresh every 5s.

**Verification:** Spawn a PM. Watch its journal grow in the dashboard.

**Step 2.5 — Live activity stream (basic).**

WebSocket at `WS /api/stream`. Pushes events of shape:

```
{"type": "agent.lifecycle", "agent_id": "...", "event": "started", "ts": "..."}
{"type": "mode.changed", "from": "build", "to": "trading", "ts": "..."}
{"type": "pm.heartbeat", "pm_id": "pm1", "mode": "build", "ts": "..."}
```

Dashboard subscribes from the home page. Events show in a small ticker at the bottom.

**Verification:** Heartbeats appear in the ticker in real time.

**Step 2.6 — Crash recovery test.**

This is the keystone test. Procedure:
1. Spawn PM1.
2. Watch a few heartbeats.
3. Kill the worker process (`kill -9` the Python worker).
4. Wait 30 seconds.
5. Restart the worker.
6. **Expected:** Temporal sees the workflow needs to continue, replays its history, and PM1 resumes heartbeats from where it left off.

If this test fails, do not move past K2. The whole architecture rests on Temporal's durability guarantee.

### Risks and tripwires

- **Activity timeouts.** Temporal activities must declare `start_to_close_timeout`. Pick generous defaults. Heartbeat in long-running activities (e.g., a 60-minute LLM call) so Temporal does not consider them dead.
- **Workflow determinism.** Workflow code must not call non-deterministic Python (no `time.time()`, no `random.random()`, no direct DB access). All non-determinism goes in activities. This is a real footgun; lean on `temporalio.workflow.now()` etc.
- **Workspace provisioning idempotency.** A PM's workspace provisioning must be idempotent in case the workflow retries. Use `mkdir -p`, do not overwrite existing files.

### Estimated time
**1 calendar week.** Most of it on Temporal patterns and the dashboard wiring.

---

## 5. Keystone 3 — Trader

### Goal
PM1 places real (paper) trades on NSE-equivalent simulated venue. Uses a fixed seed strategy (no LLM, no evolution yet). The trading mode loop works end-to-end.

### Why now
We need to prove the trading core (NautilusTrader) works in our orchestration before we add LLM reasoning on top. Get the deterministic part right first.

### Definition of done
- During NSE trading hours (or via mode override), PM1 reads market data, generates signals from a hard-coded momentum strategy, places paper orders.
- Trades persist to `paper_trades` table in Postgres.
- The dashboard shows open positions and a P&L number for PM1.
- A nightly summary lands in `/pms/pm1/journals/<date>.md`.
- The kill switch, when activated, blocks new orders within seconds. Existing positions are unaffected (they will be closed by their own SL/TP rules).

### Components built
- NautilusTrader integration at `apps/propfirm/trading/`.
- Market data adapter for NSE: 1-minute bars from a paid provider OR cached CSVs from yfinance for offline dev. Pluggable.
- Seed strategy `apps/propfirm/seed_strategies/momentum_v1.py` — a NautilusTrader `Strategy` subclass.
- Paper venue config (NautilusTrader's built-in `SimulatedVenue` with NSE specs).
- `broker` tool: `submit_order(pm_id, order)` — checks kill switch, NautilusTrader risk gates, then submits.
- `market_data` tool: `get_quote(symbol)`, `get_bars(symbol, timeframe, count)`.
- `trades` table in Postgres + `record_trade` activity.
- Trading loop activity in PM supervisor.
- Dashboard: positions table, P&L card on PM detail page.

### Components NOT built (deferred)
- LLM reasoning in the PM. The strategy is fixed.
- Strategy evolution. No version YAML yet (just the Python file).
- Risk manager beyond NautilusTrader's defaults.
- Live broker (Zerodha). Paper venue only.
- Per-PM watchlist customization. PM1 trades NIFTY 50.

### Detailed steps

**Step 3.1 — NautilusTrader bootstrap.**

Add NautilusTrader to deps. Create `apps/propfirm/trading/engine.py` that constructs a `TradingNode` with:
- `SimulatedVenue` for "NSE-PAPER".
- Instruments: NIFTY 50 stocks loaded from a JSON file.
- Risk engine with conservative defaults (max 5% per position, max 30% total exposure).
- Data engine plugged into the market data adapter.

Run a smoke test that loads, runs a one-bar backtest on a fixed dataset, prints "OK".

**Verification:** `python -m apps.propfirm.trading.smoke` outputs trade activity.

**Step 3.2 — Market data adapter.**

`apps/propfirm/data/nse.py`:
- For dev: read 1-minute bars from local parquet files (you can seed these from the existing `trading-framework/stocks/*/price_history.parquet`).
- For prod (later): pluggable interface; can swap in Groww, NSE direct, or a paid provider.
- `MarketDataAdapter.snapshot(symbols)` returns latest quote per symbol.
- `MarketDataAdapter.bars(symbol, timeframe, n)` returns last n bars.

**Verification:** `python -m apps.propfirm.data.nse RELIANCE 5` prints last 5 bars.

**Step 3.3 — Seed strategy.**

`apps/propfirm/seed_strategies/momentum_v1.py`:
- A NautilusTrader `Strategy` subclass.
- On every 1-minute bar: compute 20-day SMA, 50-day SMA. If 20 > 50 and price > 20-SMA, signal LONG.
- Position sizing: 5% of capital per position, max 5 positions.
- ATR-based stop loss at 2x ATR.
- Exit on SMA crossdown or stop-loss hit.

Deliberately simple. Not meant to make money — meant to exercise the plumbing.

**Verification:** Run a 1-month backtest using NautilusTrader's backtest engine on cached data. Confirm trades happen.

**Step 3.4 — broker tool.**

`platform/tools/broker.py`:

```python
async def submit_order(pm_id: str, order: Order) -> OrderResult:
    if await is_kill_switch_active():
        raise BrokerError("kill switch active")
    if not await check_pm_paused(pm_id):
        raise BrokerError("PM is paused")

    # Hand off to NautilusTrader
    result = await nautilus_node.submit(order, pm_id=pm_id)
    await record_trade(pm_id, order, result)
    return result
```

`record_trade` writes to `paper_trades`:

```sql
CREATE TABLE paper_trades (
    id              BIGSERIAL PRIMARY KEY,
    pm_id           TEXT NOT NULL REFERENCES pms(id),
    symbol          TEXT NOT NULL,
    side            TEXT NOT NULL,
    quantity        INT NOT NULL,
    entry_price     NUMERIC,
    entry_ts        TIMESTAMPTZ,
    stop_loss       NUMERIC,
    target          NUMERIC,
    exit_price      NUMERIC,
    exit_ts         TIMESTAMPTZ,
    outcome         TEXT,                     -- 'open' | 'sl_hit' | 'target_hit' | 'eod_close' | 'manual'
    pnl_inr         NUMERIC,
    pnl_pct         NUMERIC,
    strategy_id     TEXT,
    metadata        JSONB
);
```

**Verification:** Unit test the kill-switch path. Integration test placing a paper order and seeing the row appear.

**Step 3.5 — Trading loop activity.**

Update `PMSupervisor`:

```python
async def run(self, pm_id: str, config: dict):
    while not self._stopped:
        mode = await activity.execute(get_current_mode, ...)
        if mode == "trading":
            await activity.execute(trading_cycle, pm_id, ...)
        else:
            await activity.execute(heartbeat, pm_id, mode, ...)   # build mode placeholder for now
        await workflow.sleep(timedelta(seconds=60))
```

`trading_cycle(pm_id)`:

```python
async def trading_cycle(pm_id: str):
    pm = await load_pm(pm_id)
    strategy = load_seed_strategy(pm.config["strategy_id"])
    market = await market_data.snapshot(pm.config["watchlist"])
    signals = strategy.generate_signals(market)
    for signal in signals:
        order = build_order(pm, signal)
        try:
            result = await broker.submit_order(pm_id, order)
            await journal_append(pm_id, f"PLACED {result.summary()}")
        except BrokerError as e:
            await journal_append(pm_id, f"REJECTED {signal.symbol}: {e}")
```

**Verification:** With manual mode override, run a trading cycle, watch trades land.

**Step 3.6 — Position tracking + EOD close.**

A separate Temporal scheduled workflow: `EodCloser` runs at 15:25 IST every trading day. Reads all open paper positions, places exit orders.

Dashboard's PM detail page shows current positions table (live LTP, unrealized P&L, SL/TP).

**Verification:** Spawn PM1, let trading mode run a full day in simulation (use mode override + accelerated time), watch positions open and close.

**Step 3.7 — Kill switch enforcement.**

Add `POST /api/kill-switch/activate` and `POST /api/kill-switch/deactivate`. The `is_kill_switch_active()` check is a Postgres SELECT (cached for 1s in-process to avoid hot-loop hammering).

**Verification:** Activate kill switch during trading. Next signal generates a rejection journal entry. Deactivate. Next signal places an order.

### Risks and tripwires

- **NautilusTrader is a learning curve.** Their strategy abstraction, instrument definitions, and venue concepts take a few days to internalize. The official examples are good. Read them.
- **Time semantics in backtest vs live.** NautilusTrader's `clock` abstraction hides this; trust it. Do not call `datetime.now()` in strategies.
- **Order rejection paths.** Risk engine, kill switch, NautilusTrader's own checks, market hours — all places an order can die. Make sure each rejection lands in the journal with a clear reason.
- **Leakage from old code.** Tempting to copy `risk_manager.py` from `trading-framework/`. Resist. Use NautilusTrader's risk engine. If we need PM-specific overrides, add them as a thin layer on top, not a competing implementation.

### Estimated time
**2 calendar weeks.** First week is mostly NautilusTrader integration. Second is the trading loop, kill switch, EOD close, and dashboard.

---


## 6. Keystone 4 — Reflection

### Goal
PM1 reads its own performance, journals reflectively using an LLM, and evolves its own strategy (commits new versions of a strategy YAML). Still no engineers — the PM writes its own strategy.

### Why now
Before delegating engineering work to a sub-agent, the PM needs to be able to reason about its performance and articulate what changes it wants. Otherwise the PM is just a router. This keystone proves the LangGraph reasoning layer works.

### Definition of done
- During build mode, PM1 runs a build cycle every hour. Each cycle:
  - Reads its plan, journal tail, recent trades, leaderboard.
  - Calls Sonnet via the AGORA LLM client.
  - Decides on an action: `DO_NOTHING`, `UPDATE_PLAN`, `EVOLVE_STRATEGY`, or `JOURNAL_ONLY`.
  - Executes the action (writes plan/strategy/journal).
- The PM has committed at least one new strategy version (`v002.yaml`) on its own initiative within 48 hours of running.
- Strategy changes are picked up at the next 09:00 freeze cutoff and used in trading mode the following day.
- Langfuse shows traces of every PM build cycle, with the full prompt and response.

### Components built
- LangGraph supervisor agent at `platform/workers/pm_agent.py`.
- Letta integration for PM memory at `platform/memory/letta_client.py`.
- Strategy YAML loader at `apps/propfirm/strategy/loader.py` — reads `/pms/<pm_id>/strategies/ACTIVE`, parses YAML, instantiates a NautilusTrader strategy.
- Strategy registry: `commit_new_version(pm_id, yaml_content)` — atomic write of a new versioned file + ACTIVE pointer.
- Build loop activity in PM supervisor.
- LLM-callable tools (the PM's tool registry, gated to its own scope):
  - `read_journal(lines)`, `read_plan()`, `read_strategy()`
  - `write_plan(content)`, `write_strategy(yaml_content)`
  - `read_trades(date_range)`, `read_leaderboard()`
  - `read_rival_journal(other_pm_id, lines)`, `read_rival_strategy(other_pm_id)` — open competition
  - `memory_store(key, value, tags)`, `memory_search(query)`
- Dashboard: strategy version history, diff view, journal viewer with reasoning summaries.

### Components NOT built (deferred)
- Engineers and engineering teams. PM still writes its own code/YAML.
- Research team.
- LLM-driven trading mode decisions. Trading mode still runs deterministically.

### Detailed steps

**Step 4.1 — Strategy YAML schema.**

```yaml
# /pms/pm1/strategies/v001.yaml
_meta:
  version: 1
  created_at: '2026-06-01T08:00:00Z'
  parent_version: null
  notes: 'Seed momentum strategy.'

name: momentum
description: 20/50-SMA crossover with ATR stop.
universe: NIFTY_50
position_sizing:
  method: fixed_pct
  pct: 5.0
risk:
  atr_multiplier: 2.0
  max_positions: 5
indicators:
  - {name: sma, period: 20, source: close}
  - {name: sma, period: 50, source: close}
  - {name: atr, period: 14}
entry:
  long_when:
    - sma_20 > sma_50
    - close > sma_20
  short_when: []         # long-only
exit:
  stop_loss: atr_multiplier * atr_14
  target: null            # let SMA cross handle
```

The seed strategy `momentum_v1.py` from K3 is reformulated as a YAML-driven NautilusTrader strategy class that interprets this schema. The class itself does not change between versions — only the YAML.

If a YAML feature requires code changes (e.g., a new indicator), that becomes an engineer task in K5. For now, the strategy class supports a small fixed vocabulary.

**Verification:** Loading `v001.yaml` produces a NautilusTrader strategy that backtests identically to the K3 hardcoded version.

**Step 4.2 — Letta integration.**

Each PM has one Letta agent. Its identity (system prompt) is the PM persona prompt. Its core memory holds:
- Block 1: identity (name, style, thesis, current strategy version).
- Block 2: standing rules (open competition, write-only-own-scope, mode discipline).

When the PM agent runs a cycle, it loads its Letta agent, sends the cycle context as a user message, and reads the response.

```python
class PMLLMAgent:
    def __init__(self, pm_id: str):
        self.pm_id = pm_id
        self.letta = letta_client.get_agent(f"pm-{pm_id}")

    async def reflect(self, context: BuildContext) -> Decision:
        prompt = render_build_prompt(context)
        response = await self.letta.send_message(prompt)
        return parse_decision(response)
```

This is what gives PMs persistence of personality across runs.

**Verification:** Spawn PM1, restart everything, check that Letta still has the PM1 agent with its core memory intact.

**Step 4.3 — Tool registry for the PM.**

Tools exposed to PM1's LLM are wrapped functions that:
- Validate scope (path-based check; PM1 cannot `write_strategy` to PM2's path).
- Log the call as a Langfuse span.
- Return structured responses.

```python
@tool(name="read_journal", scope="self")
async def read_journal(pm_id: str, lines: int = 50) -> str:
    """Read the last N lines of this PM's journal."""
    path = f"pms/{pm_id}/journals/{today()}.md"
    return tail(path, lines)

@tool(name="read_rival_journal", scope="any_pm")
async def read_rival_journal(self_pm_id: str, other_pm_id: str, lines: int = 50) -> str:
    """Read another PM's journal (open competition)."""
    if other_pm_id == self_pm_id:
        raise ToolError("use read_journal for your own journal")
    path = f"pms/{other_pm_id}/journals/{today()}.md"
    return tail(path, lines)

@tool(name="write_strategy", scope="self")
async def write_strategy(pm_id: str, yaml_content: str) -> str:
    """Commit a new strategy version. Returns the new version id."""
    parsed = yaml.safe_load(yaml_content)
    validate_strategy_schema(parsed)
    new_version = await registry.commit_new_version(pm_id, yaml_content)
    return f"committed v{new_version:03d}"
```

The LangGraph build loop binds these as available tools.

**Verification:** Manually invoke each tool from a Python REPL with PM1 context, confirm scope enforcement.

**Step 4.4 — LangGraph build agent.**

```python
def build_pm_graph():
    builder = StateGraph(BuildState)
    builder.add_node("read_state", read_state_node)
    builder.add_node("read_rivals", read_rivals_node)
    builder.add_node("reflect", reflect_node)             # the LLM call
    builder.add_node("execute_action", execute_action_node)
    builder.add_node("journal_cycle", journal_cycle_node)
    builder.set_entry_point("read_state")
    builder.add_edge("read_state", "read_rivals")
    builder.add_edge("read_rivals", "reflect")
    builder.add_edge("reflect", "execute_action")
    builder.add_edge("execute_action", "journal_cycle")
    builder.set_finish_point("journal_cycle")
    return builder.compile()
```

`reflect_node` issues a single LLM call with the tools available. The LLM either calls a tool (read additional context) or returns a structured `Decision` JSON. If it called a tool, we loop back into `reflect`. Cap iterations at 10.

The decision schema:

```python
class Decision(BaseModel):
    action: Literal["DO_NOTHING", "UPDATE_PLAN", "EVOLVE_STRATEGY", "JOURNAL_ONLY"]
    reasoning: str
    plan_md: str | None = None        # required for UPDATE_PLAN
    strategy_yaml: str | None = None  # required for EVOLVE_STRATEGY
```

**Verification:** Spawn PM1, force a build cycle (manual trigger endpoint), see a decision land. Inspect the trace in Langfuse.

**Step 4.5 — Build loop activity in PMSupervisor.**

```python
async def run(self, pm_id: str, config: dict):
    while not self._stopped:
        mode = await activity.execute(get_current_mode, ...)
        if mode == "trading":
            await activity.execute(trading_cycle, pm_id, ...)
            await workflow.sleep(timedelta(seconds=60))
        elif mode == "build":
            await activity.execute(build_cycle, pm_id, ...)
            await workflow.sleep(timedelta(minutes=60))     # build cycles are hourly
        else:                                                # pre_trade_freeze
            await workflow.sleep(timedelta(seconds=60))
```

`build_cycle(pm_id)` invokes the LangGraph and persists the resulting decision.

**Verification:** Run a PM through a full day-night cycle. Confirm trading cycles every minute during market hours and build cycles every hour off-hours. No build cycles between 09:00 and 15:30.

**Step 4.6 — Dashboard: strategy + journal views.**

`/pms/[id]/strategy`: shows current ACTIVE strategy YAML, version history list, diff between consecutive versions.

`/pms/[id]/journal`: append-only journal, scrollable, with toggleable verbosity (raw lines vs grouped by cycle with reasoning summaries).

**Verification:** Use the dashboard to read PM1's reasoning. Should be legible.

### Risks and tripwires

- **Cost spikes on the LLM.** A misbehaving build loop could call Sonnet hundreds of times per cycle. Set a hard cap per cycle (10 LLM calls), per day ($20 default), and a soft warning at 80%.
- **Loop in tool calls.** PM might keep calling `read_journal` forever. The 10-iteration cap on the reflect node prevents this.
- **YAML validation.** PM might write malformed YAML or schema-violating YAML. Validate strictly. Reject and journal the rejection. Do not let a bad YAML ship to trading mode.
- **The PM might write something incoherent.** That is fine. It learns from its own bad decisions across cycles. Letta retains memory of "I tried this approach last week and lost 3%."

### Estimated time
**1 calendar week.** Most of it on tool wiring and prompt iteration.

---

## 7. Keystone 5 — Engineer

### Goal
PM1 spawns one engineer agent. The engineer runs in an E2B sandbox, makes a code change to PM1's workspace, opens a PR. Human reviews, merges, code goes live next day.

### Why now
This is the single hardest keystone. It is the first time agents write real code in real sandboxes and that code lands in production. If we get this right, hierarchy (K6) and PM2 (K7) are mostly variations on this theme.

### Definition of done
- PM1 issues `SPAWN_ENGINEER` action with a task spec like "Add a 200-day SMA filter to the strategy: only enter long when price > sma_200."
- The task creates a Temporal workflow that:
  - Provisions an E2B sandbox.
  - Clones the monorepo, checks out a fresh branch named `pm1/eng-task-<id>`.
  - Runs OpenHands inside the sandbox with the task spec, scoped to `/pms/pm1/`.
  - OpenHands writes code, runs tests until they pass.
  - AGORA CI runs (lint, types, tests, path-scope check, backtest equivalence).
  - On success, opens a PR via the GitHub App.
  - Tears down the sandbox.
- The PR appears in the dashboard's `/prs` page.
- I (the human) merge it on the dashboard.
- The next build cycle for PM1 sees the new code.
- The next trading day uses the new strategy.

### Components built
- E2B integration at `platform/sandbox/e2b.py` — provision, copy files in, exec, copy out, destroy.
- OpenHands wrapper at `platform/workers/engineer.py` — wraps OpenHands' agent in a Temporal activity.
- AGORA CI scripts at `ci/` — `lint.sh`, `types.sh`, `unit.sh`, `path_scope.py`, `backtest_equivalence.py`.
- GitHub App for PR creation. Per-PM bot accounts (`agora-pm1-bot`).
- `EngineerTaskWorkflow` in Temporal.
- `tasks` and `prs` tables in Postgres.
- PR review surface in dashboard.
- New tool: `spawn_engineer_task` (PM-callable).

### Components NOT built (deferred)
- Eng Head (will be added in K6). For now, PM directly spawns single engineer task workflows.
- Multiple engineers in parallel. One at a time for K5.
- Auto-merge. Every PR requires manual review in K5.
- Research team.

### Detailed steps

**Step 5.1 — GitHub App setup.**

Create a GitHub App with permissions:
- Contents: read/write
- Pull requests: read/write
- Metadata: read

Install on the AGORA monorepo. Generate a private key, store securely. Create one bot account per PM (`agora-pm1-bot`); the GitHub App can author PRs as itself (preferred) or commits can be signed by per-PM identities. Start with the App as author; add per-PM identity later if useful.

`platform/integrations/github.py`:
- `clone_to_sandbox(sandbox, branch_name)` — uses an installation token.
- `open_pr(pm_id, branch_name, title, body)` — opens a PR targeting `main`, returns PR number.
- `add_comment(pr_number, body)` — for review feedback.

**Verification:** Run a manual smoke test that opens a PR, comments on it, closes it.

**Step 5.2 — E2B sandbox primitive.**

`platform/sandbox/e2b.py`:

```python
class Sandbox:
    @classmethod
    async def create(cls, template: str = "agora-engineer") -> "Sandbox":
        sb = await e2b_client.sandboxes.create(template=template)
        return cls(sb)

    async def upload(self, local_path: str, remote_path: str): ...
    async def download(self, remote_path: str, local_path: str): ...
    async def exec(self, cmd: str, timeout: int = 300) -> ExecResult: ...
    async def destroy(self): ...
```

Build the `agora-engineer` E2B template with: Python 3.13, uv, ripgrep, git, GitHub CLI, plus your project's dev dependencies.

**Verification:** Smoke test creates a sandbox, runs `python -V`, destroys it.

**Step 5.3 — AGORA CI scripts.**

`ci/path_scope.py`:

```python
def main(pm_id: str, base_ref: str = "main"):
    diff = subprocess.run(
        ["git", "diff", "--name-only", base_ref], capture_output=True, text=True
    ).stdout.splitlines()
    allowed_prefix = f"pms/{pm_id}/"
    violations = [f for f in diff if not f.startswith(allowed_prefix)]
    if violations:
        print("PATH SCOPE VIOLATION:")
        for f in violations:
            print(f"  {f} not under {allowed_prefix}")
        sys.exit(1)
    print("path scope OK")
```

`ci/backtest_equivalence.py`: runs the PM's previous strategy and the proposed new strategy on the same backtest dataset. Diff metrics. If the new strategy diverges drastically from the old (e.g., total return differs by >50%), flag it as a reviewer warning, not a failure.

`ci/unit.sh`, `ci/lint.sh`, `ci/types.sh`: thin wrappers around pytest, ruff, mypy.

**Verification:** Manually create a violating PR (touch a path outside `/pms/pm1/`), confirm the path-scope check fails. Revert.

**Step 5.4 — OpenHands wrapper.**

OpenHands has a Python SDK. Wrap it:

```python
class EngineerAgent:
    def __init__(self, sandbox: Sandbox, pm_id: str, task: TaskSpec, model: str):
        self.sandbox = sandbox
        self.pm_id = pm_id
        self.task = task
        self.model = model

    async def run(self) -> EngineerResult:
        prompt = render_engineer_prompt(self.pm_id, self.task)
        agent = OpenHandsAgent(
            workspace_dir="/workspace",
            model=self.model,
            tools=DEFAULT_TOOLS,         # OpenHands' shell, file edit, browser, etc.
        )
        result = await agent.run(prompt, sandbox=self.sandbox)
        return EngineerResult(
            files_changed=result.files_changed,
            commits=result.commits,
            test_output=result.test_output,
            success=result.success
        )
```

The prompt to OpenHands:

```
You are an engineer working for PM1.

TASK: {task.spec}

WORKSPACE: You may modify any file under /workspace/pms/pm1/.
You may NOT modify files outside /workspace/pms/pm1/.

WORKFLOW:
1. Read /workspace/pms/pm1/strategies/ACTIVE to see the current strategy.
2. Make the requested change.
3. Run `pytest /workspace/pms/pm1/tests/` until all tests pass.
4. Run `bash /workspace/ci/path_scope.py pm1` to verify scope.
5. Commit your changes on the current branch.
6. Provide a summary of what you did and why.

CONSTRAINTS:
- Do not run pip install of new dependencies without flagging it.
- Do not modify files outside /workspace/pms/pm1/.
- Tests must pass before you are done.
```

**Verification:** Run a sandbox locally, hand it a trivial task ("add a comment to v001.yaml saying hello"), confirm OpenHands succeeds and the file changes.

**Step 5.5 — EngineerTaskWorkflow.**

```python
@workflow.defn
class EngineerTaskWorkflow:
    @workflow.run
    async def run(self, pm_id: str, task_spec: TaskSpec) -> EngineerTaskResult:
        task_id = await activity.execute(register_task, pm_id, task_spec, ...)
        try:
            sandbox_id = await activity.execute(create_sandbox, task_id, ...)
            await activity.execute(clone_repo_to_sandbox, sandbox_id, pm_id, task_id, ...)
            engineer_result = await activity.execute(
                run_engineer, sandbox_id, pm_id, task_spec,
                start_to_close_timeout=timedelta(minutes=30),
                heartbeat_timeout=timedelta(minutes=5)
            )
            if not engineer_result.success:
                await activity.execute(record_task_failure, task_id, engineer_result, ...)
                return EngineerTaskResult(status="failed", ...)
            ci_result = await activity.execute(run_ci, sandbox_id, pm_id, ...)
            if not ci_result.passed:
                await activity.execute(record_task_failure, task_id, ci_result, ...)
                return EngineerTaskResult(status="ci_failed", ...)
            pr_number = await activity.execute(open_pr, sandbox_id, pm_id, task_id, ...)
            await activity.execute(record_pr, task_id, pr_number, ...)
            return EngineerTaskResult(status="pr_opened", pr_number=pr_number)
        finally:
            await activity.execute(destroy_sandbox, sandbox_id, ...)
```

The activity timeouts and heartbeats matter here — engineer runs are minutes long.

**Verification:** Trigger an engineer task end-to-end. Watch the Temporal Web UI. Confirm a PR appears on GitHub.

**Step 5.6 — Schemas + Postgres.**

```sql
CREATE TABLE tasks (
    id              TEXT PRIMARY KEY,
    pm_id           TEXT NOT NULL,
    parent_agent_id TEXT,
    kind            TEXT NOT NULL,            -- 'engineer' | 'researcher'
    spec            TEXT NOT NULL,
    status          TEXT NOT NULL,            -- 'queued' | 'running' | 'pr_opened' | 'failed' | 'completed'
    started_at      TIMESTAMPTZ NOT NULL,
    completed_at    TIMESTAMPTZ,
    result          JSONB
);

CREATE TABLE prs (
    id              BIGSERIAL PRIMARY KEY,
    task_id         TEXT REFERENCES tasks(id),
    pm_id           TEXT NOT NULL,
    github_pr_number INT NOT NULL,
    branch_name     TEXT NOT NULL,
    title           TEXT,
    summary         TEXT,
    status          TEXT NOT NULL,            -- 'open' | 'merged' | 'closed_unmerged'
    ci_status       TEXT,                     -- 'pending' | 'pass' | 'fail'
    eng_head_review TEXT,
    auto_merge_eligible BOOL DEFAULT FALSE,
    opened_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    merged_at       TIMESTAMPTZ
);
```

**Step 5.7 — Mode-aware merge gating.**

In K2 we built the mode controller. Now we use it. The PR merge endpoint checks mode:

```python
@router.post("/api/prs/{id}/merge")
async def merge_pr(pr_id: int):
    mode = await get_current_mode()
    if mode != "build":
        raise HTTPException(409, f"cannot merge in mode={mode}; merges allowed in build mode only")
    # ... actually merge ...
```

A scheduled Temporal workflow `PRAutoMergeChecker` runs every 5 minutes. It re-evaluates auto-merge-eligible PRs (none yet — that lands in K8). For now, just enforce no merges during freeze + trading mode.

**Verification:** During trading mode, attempt to merge a PR via the API. Get 409. Try after market close. Succeeds.

**Step 5.8 — Dashboard PR queue.**

`/prs` page. Card per PR with:
- PM and task spec.
- Diff size.
- CI status.
- Buttons: Merge / Reject / View on GitHub.

`/prs/[id]` detail page: full diff (pulled via GitHub API), CI logs, eng-head review (empty in K5).

**Verification:** Run a few engineer tasks. Watch PRs appear. Merge one via the dashboard. Confirm it merges on GitHub.

**Step 5.9 — `spawn_engineer_task` tool.**

Add the tool to PM1's available tools in K4:

```python
@tool(name="spawn_engineer_task", scope="self")
async def spawn_engineer_task(pm_id: str, spec: str) -> str:
    """Spawn an engineer to write code in your workspace.
    Returns the task id. The task runs asynchronously."""
    handle = await temporal_client.start_workflow(
        EngineerTaskWorkflow.run, pm_id, TaskSpec(spec=spec),
        id=f"task-{uuid4()}",
        task_queue="agora",
    )
    return handle.id
```

The PM's build cycle now has a real `SPAWN_ENGINEER` action it can take.

**Verification:** Manually inject a build cycle for PM1 with a context that should result in spawning an engineer. Watch the engineer run, the PR open, the dashboard surface it.

### Risks and tripwires

- **OpenHands cost.** A long engineer run can burn $5-10 of LLM tokens. Set hard caps in OpenHands config (max iterations, max tokens). The activity's heartbeat lets Temporal kill runaways.
- **Engineer escapes the path scope.** OpenHands might try to install packages or modify files outside `/workspace/pms/pm1/`. The CI path-scope check catches it but only after the engineer is done. To make the rule stick, also enforce at the sandbox FS level: read-only mount of `/workspace` except for `/workspace/pms/pm1/`. E2B supports this.
- **PR with broken backtest.** Engineer wrote code that "passes tests" but the strategy is now nonsense. Backtest equivalence check is your second line of defense. If backtest diverges, PR gets a warning label and you eyeball it carefully.
- **GitHub rate limits.** With multiple engineers running, you can hit secondary rate limits. Use installation tokens (higher limits than personal tokens) and back off on 429s.

### Estimated time
**2 calendar weeks.** Real-world experience: anywhere from "wow, this just worked" to "OpenHands keeps trying to apt-get install things." Plan for two weeks; ship in one if lucky.

---

## 8. Keystone 6 — Hierarchy

### Goal
PM1 spawns an Eng Head agent, which spawns multiple engineers in parallel, which open PRs that the Eng Head triages before forwarding to the human queue.

### Why now
We have proven one engineer works. Now we prove the recursive pattern. The Eng Head pattern, once working, generalizes to research teams (just a different output type).

### Definition of done
- PM1's tool registry includes `spawn_eng_head_task(spec)` instead of `spawn_engineer_task`. The PM no longer spawns engineers directly.
- The Eng Head is a persistent agent. First call creates it; subsequent calls reuse it.
- Eng Head can decompose a task into multiple engineer sub-tasks and spawn them in parallel.
- Engineer PRs go to the Eng Head first, not the human.
- Eng Head reviews each PR, posts a review comment, decides "approve and forward to human" or "reject and re-spawn with adjusted spec."
- Dashboard shows the agent tree: PM1 → EngHead → Engineers.

### Components built
- Eng Head agent at `platform/workers/eng_head.py` — a LangGraph supervisor.
- `EngHeadWorkflow` — long-lived Temporal workflow per Eng Head, takes signals to delegate work.
- Updated `EngineerTaskWorkflow` to report up to its Eng Head parent.
- New tool: `delegate_to_engineer(sub_spec)` — Eng-Head-callable.
- New tool: `review_pr(pr_number, decision, comment)` — Eng-Head-callable.
- Dashboard: tree visualization of the agent hierarchy.

### Components NOT built (deferred)
- Research team. Same pattern; will be K9 (post-v1).
- Cross-team coordination (Eng Head talks to Research Lead). Not needed yet.

### Detailed steps

**Step 6.1 — EngHeadWorkflow (long-lived).**

```python
@workflow.defn
class EngHeadWorkflow:
    @workflow.run
    async def run(self, pm_id: str):
        await activity.execute(register_eng_head_agent, pm_id, ...)
        try:
            while not self._stopped:
                # Wait for tasks to be queued
                task = await self._next_task()       # uses workflow signals
                child = await workflow.execute_child_workflow(
                    EngHeadTaskWorkflow.run, pm_id, task,
                    id=f"eng-head-task-{task.id}"
                )
        finally:
            await activity.execute(deregister_eng_head_agent, pm_id, ...)
```

Spawned lazily by the PM the first time it issues `SPAWN_ENG_HEAD_TASK`. Persists across PM cycles.

**Step 6.2 — EngHead reasoning (decomposition).**

`platform/workers/eng_head.py`:

```python
async def decompose(spec: str, model: str) -> list[SubTask]:
    """Use the LLM to break a task into engineer-scoped sub-tasks."""
    prompt = f"""
    Decompose this engineering task into 1–4 independent sub-tasks
    that can be implemented in parallel by separate engineers.

    PARENT TASK: {spec}

    Each sub-task should be:
    - Self-contained (no shared state with siblings)
    - Implementable in <500 lines of code
    - Testable in isolation

    Return JSON: [{{"id": ..., "spec": ...}}, ...]
    """
    response = await llm_call(model, prompt)
    return parse_sub_tasks(response)
```

**Step 6.3 — Parallel engineer spawn.**

```python
async def execute_eng_head_task(pm_id: str, parent_task: TaskSpec):
    sub_tasks = await decompose(parent_task.spec, model="sonnet-4-5")

    # Launch engineer workflows in parallel
    engineer_handles = []
    for st in sub_tasks:
        handle = await workflow.execute_child_workflow(
            EngineerTaskWorkflow.run, pm_id, st,
            id=f"engineer-{st.id}",
            parent_close_policy=ParentClosePolicy.TERMINATE
        )
        engineer_handles.append(handle)

    # Wait for all to complete
    engineer_results = await asyncio.gather(*[h.result() for h in engineer_handles])

    # Review each PR
    for st, result in zip(sub_tasks, engineer_results):
        if result.status == "pr_opened":
            await review_engineer_pr(pm_id, result.pr_number, st.spec)
```

**Step 6.4 — Eng Head PR review.**

```python
async def review_engineer_pr(pm_id: str, pr_number: int, original_spec: str):
    diff = await github.get_pr_diff(pr_number)
    ci = await github.get_pr_ci_status(pr_number)
    prompt = f"""
    Review this PR.

    ORIGINAL SPEC: {original_spec}

    DIFF: {diff}
    CI: {ci.summary}

    Decide:
    - APPROVE: forward to human review queue.
    - REJECT: close the PR. Re-spawn with corrected spec.
    - APPROVE_WITH_NOTES: forward but flag concerns.

    Respond with JSON: {{"decision": ..., "notes": ..., "respawn_spec": ...}}
    """
    decision = await llm_call("sonnet-4-5", prompt)
    if decision.action == "REJECT":
        await github.close_pr(pr_number)
        await spawn_new_engineer_task(pm_id, decision.respawn_spec)
        return
    await github.add_comment(pr_number, render_review(decision))
    await db.update_pr(pr_number, {
        "eng_head_review": decision.notes,
        "status": "open",                         # human can now merge
    })
```

**Step 6.5 — Dashboard tree view.**

`/pms/[id]` shows the hierarchy:

```
PM1 ── thinking
 ├── EngHead ── reviewing PR #42 (sonnet-4.5, 8s)
 │    ├── Engineer-task-7 ── DONE (PR #42)
 │    ├── Engineer-task-8 ── thinking (sonnet-4.5, 14s)
 │    └── Engineer-task-9 ── DONE (PR #44)
 └── (no research team yet)
```

Each node is clickable; opens an agent detail panel.

**Step 6.6 — Lifecycle propagation.**

When PM1 is stopped, its EngHead workflow is terminated, which cascades to active engineer workflows (Temporal's `parent_close_policy=TERMINATE`).

When PM1 is paused, it stops issuing new tasks. In-flight engineer work continues (we let it finish; paused != killed).

**Verification:** Stop PM1 mid-engineering-task. Confirm all child workflows terminate. Postgres shows them as `terminated`.

### Risks and tripwires

- **Decomposition quality.** Eng Head might split a task badly: redundant sub-tasks or sub-tasks with hidden dependencies. Iterate on the decomposition prompt with examples.
- **Race conditions on the same files.** If two engineers in the same Eng Head batch try to modify the same file, Git will conflict at PR merge time. Eng Head's decomposition prompt must explicitly require non-overlapping file scopes; CI re-checks.
- **Cost amplification.** 4 engineers in parallel = 4x token spend per task. The per-PM daily budget cap is critical.

### Estimated time
**1 calendar week.**

---

## 9. Keystone 7 — Genesis

### Goal
You can spawn PM2 from the dashboard with a one-line instruction. PM2 cold-starts from blank, picks its strategy (using the cold-start menu), and starts trading. Two PMs compete on the same leaderboard.

### Why now
We need to prove the platform is multi-PM, not just one-PM-with-extras. PM2's existence stresses the namespace isolation, the leaderboard, the cost dashboards.

### Definition of done
- `POST /api/pms/spawn -d '{"name": "PM2", "instruction": "..."}'` creates PM2.
- PM2's first build cycle sees the cold-start menu in its prompt and chooses one of the four options.
- PM2 writes its first strategy version to `/pms/pm2/strategies/v001.yaml` autonomously.
- PM2 starts trading the next trading day.
- The leaderboard view at `/` shows both PMs ranked.
- Each PM's costs are tracked separately in `/budget`.
- A 2-day soak test (paper trading) shows neither PM crashes, both produce trades, both have non-trivial journals.

### Components built
- Cold-start menu injection at `apps/propfirm/prompts/cold_start.md`.
- Leaderboard SQL view + dashboard widget.
- Per-PM cost dashboard.
- Documentation for "how to spawn a new PM" in `2.0/README.md`.

### Components NOT built (deferred)
- Research team (still deferred).
- Auto-merge (K8).

### Detailed steps

**Step 7.1 — Cold-start prompt injection.**

The PM agent's reflect node detects "this is the first cycle" (no prior journal entries, no strategy yet) and injects the cold-start menu (text from §8.2 of the framework doc) into the LLM prompt.

The PM picks one of A/B/C/D, writes a brief reasoning to `/pms/pm2/journals/cold_start.md`, then proceeds with the chosen path.

**Step 7.2 — Leaderboard view.**

```sql
CREATE VIEW leaderboard AS
SELECT
    pms.id AS pm_id,
    pms.name,
    pms.starting_capital_inr,
    COALESCE(SUM(t.pnl_inr) FILTER (WHERE t.outcome != 'open'), 0) AS realized_pnl,
    pms.starting_capital_inr + COALESCE(SUM(t.pnl_inr) FILTER (WHERE t.outcome != 'open'), 0) AS current_capital,
    100.0 * COALESCE(SUM(t.pnl_inr) FILTER (WHERE t.outcome != 'open'), 0) / pms.starting_capital_inr AS pct_return,
    COUNT(*) FILTER (WHERE t.outcome != 'open') AS closed_trades,
    100.0 * COUNT(*) FILTER (WHERE t.outcome != 'open' AND t.pnl_inr > 0)
        / NULLIF(COUNT(*) FILTER (WHERE t.outcome != 'open'), 0) AS win_rate_pct
FROM pms
LEFT JOIN paper_trades t ON t.pm_id = pms.id
WHERE pms.status != 'stopped'
GROUP BY pms.id, pms.name, pms.starting_capital_inr
ORDER BY pct_return DESC NULLS LAST;
```

Surfaced on the dashboard home page.

**Step 7.3 — Per-PM budget view.**

```sql
SELECT pm_id,
       SUM(amount_usd) AS total_spent_usd,
       SUM(amount_usd) FILTER (WHERE ts > now() - interval '1 day') AS spent_today_usd
FROM budget_events
GROUP BY pm_id;
```

`/budget` page shows each PM's daily and total spend, with cap warnings.

**Step 7.4 — Soak test.**

Run both PMs for at least 2 calendar days, ideally a full trading week. Watch for:
- Crashes (none allowed; if one crashes, fix before declaring K7 done).
- Both PMs produce at least one trade.
- Both PMs accumulate journal entries.
- Cost stays within budget.

This is a calendar-bound part of the keystone. The clock is the test.

### Risks and tripwires

- **PM2's cold start might be incoherent.** Sonnet 4.5 should be fine but is not guaranteed. If PM2 produces a malformed `v001.yaml`, the strategy loader rejects it; PM2 lands in an error loop. Cap at 3 attempts; if PM2 cannot produce a valid strategy, mark it as "stuck" and surface to the human.
- **Two PMs might collide on the same trades.** Paper market, not a real concern. But the journals will look amusing. Worth watching.

### Estimated time
**1 calendar week** (mostly waiting for the soak test).

---

## 10. Keystone 8 — Hardening

### Goal
The system survives operational reality. Cost caps work. Kill switch works. Auto-merge works for trivial PRs. The dashboard has the polish to be your daily cockpit. A documented recovery drill exists.

### Why now
Everything before this was "happy path." K8 is "what happens when things go wrong." Without it, the system is fragile and you cannot leave it unattended.

### Definition of done
- A PM hitting its daily budget cap stops calling LLMs and journals "budget exhausted."
- The kill switch, when activated, blocks all orders within 5 seconds. Tested by a dedicated fault-injection test.
- Auto-merge works: a PR labeled "trivial" (small diff, scoped to journals/research/, CI green) is merged automatically after a 5-minute delay if no human action.
- A "recovery drill" doc exists describing how to restart the entire system from cold and what to verify.
- The dashboard has a "system health" overview showing all critical services and their status.
- Alembic migrations have backward-compatibility tests (each migration can be rolled back cleanly).
- Total cost dashboard projection: at current burn rate, days of runway remaining.

### Components built
- Budget enforcement middleware in the AGORA LLM client (raises `BudgetExhausted` when over cap).
- Kill switch fault injection test.
- `PRAutoMergeChecker` workflow with the eligibility logic.
- System health view.
- Recovery drill doc at `2.0/plan/runbooks/cold_start.md`.
- Alembic downgrade tests.

### Components NOT built (deferred)
- Production deployment automation. K8 is local-first; production is K9 or its own track.
- Research team. Still deferred.
- Live broker (Zerodha). Still paper-only.

### Detailed steps

**Step 8.1 — Budget enforcement.**

In the AGORA LLM client:

```python
async def call(self, ...):
    spent = await db.fetchval(
        "SELECT COALESCE(SUM(amount_usd), 0) FROM budget_events "
        "WHERE pm_id = :pm_id AND ts >= :start", pm_id=self.pm_id, start=today_start_utc()
    )
    cap = await get_pm_daily_cap(self.pm_id)
    if spent >= cap:
        await journal_append(self.pm_id, f"BUDGET EXHAUSTED ({spent:.2f}/{cap:.2f}). Build mode suspended.")
        raise BudgetExhausted(spent=spent, cap=cap)
    if spent >= 0.8 * cap and not already_warned_today(self.pm_id):
        await journal_append(self.pm_id, f"BUDGET WARNING: {spent:.2f}/{cap:.2f} (80% used).")
    # Proceed with call
```

**Step 8.2 — Auto-merge logic.**

A PR is auto-merge-eligible when ALL of:
- CI status is green.
- Diff is < 100 lines.
- Files touched are all in:
  - `/pms/<pm_id>/journals/`
  - `/pms/<pm_id>/research/`
  - `/pms/<pm_id>/plans/`
  - `/pms/<pm_id>/strategies/v<NNN>.yaml` (only YAML, not the strategy class)
- Eng Head has reviewed and approved (or there is no Eng Head and it is a PM-direct PR).
- Mode is `build`.

`PRAutoMergeChecker` runs every 5 minutes:

```python
@workflow.defn
class PRAutoMergeChecker:
    @workflow.run
    async def run(self):
        while True:
            eligible = await activity.execute(find_auto_merge_candidates, ...)
            for pr in eligible:
                age = now() - pr.opened_at
                if age >= timedelta(minutes=5):
                    await activity.execute(auto_merge_pr, pr.id, ...)
            await workflow.sleep(timedelta(minutes=5))
```

The 5-minute delay gives you a chance to intervene.

**Step 8.3 — Recovery drill.**

`2.0/plan/runbooks/cold_start.md`:

```markdown
# AGORA Cold Start Drill

When everything is dead and you need to bring it back.

## Pre-conditions
- You have access to the host machine.
- Postgres data volume is intact (otherwise, this is a different doc).

## Steps
1. `docker compose down --volumes` (only if you really want to wipe; ordinarily skip)
2. `make up`
3. Wait for `make health` to show all green.
4. Resume PMs:
   - `agora-cli pms list` → see status of each PM.
   - For each "stopped" PM you want to bring back: `agora-cli pms resume <id>`.
5. Verify in dashboard:
   - Mode controller is correct for current time.
   - Each PM shows recent journal activity within 2 minutes.
   - Leaderboard query works.
6. If a PM is stuck (in "error" state):
   - Read its last journal.
   - Read its Letta state.
   - Decide: re-spawn fresh or repair.

## Common issues
- "Temporal workflow not found": the workflow id changed between deploys. Update DB or re-spawn the PM.
- "Letta agent missing": check Letta server is up, re-attach if needed.
- "Path scope check fails on benign PR": git rebase from main may have stale changes; re-trigger.
```

Run this drill once. End to end. Document anything you found surprising.

**Step 8.4 — Migration safety tests.**

Each Alembic migration has a `downgrade` function. CI runs:

```
alembic upgrade head
alembic downgrade -1
alembic upgrade head
```

This catches reversible-migration bugs early.

**Step 8.5 — System health view.**

`/admin` page shows:
- Postgres connection: OK / latency.
- Temporal connection: OK / pending workflows count.
- Langfuse connection: OK.
- Letta connection: OK.
- Qdrant connection: OK.
- E2B remaining quota: X sandbox-minutes available.
- LLM provider status (per provider in your routing table).
- Active alerts.

### Risks and tripwires

- **Auto-merge bites you eventually.** Even with conservative rules, an auto-merged PR will at some point cause a problem. The recovery drill should include "revert auto-merged PR." Plan for it.
- **Budget caps too tight.** First few days you might hit caps faster than expected. Monitor and tune.
- **Dashboard polish never ends.** Resist. The dashboard is good enough when you can run a 2-week trial without dropping to SQL.

### Estimated time
**1 calendar week.**

---


## 11. Cross-Cutting Concerns

These are not keystones because they touch every keystone. They are practices, not milestones.

### 11.1 Testing strategy

Three test layers, increasing in cost and decreasing in count:

**Unit tests (per module):** every utility, every parser, every business rule. Run on every PR. Fast (< 30s total). Goal: 80%+ coverage of business logic, less for glue.

**Integration tests (per service boundary):** spin up Postgres + Temporal in testcontainers. Test that workflows complete, that activities write to the DB correctly, that signals propagate. Run on every PR. Slower (~5 minutes).

**End-to-end soak tests (per keystone):** run the full system against a fixed dataset and assert invariants over a simulated day-week. Run nightly, not per PR. Slowest (15-60 minutes).

There are NO production manual tests. If something is worth checking, it is worth a test.

### 11.2 Secrets management

Local dev: `.env` file at the workspace root. Loaded by `python-dotenv`. The `.env.example` file lists every required key by name only.

Production: AWS Secrets Manager + IAM-scoped retrieval. Keys are loaded at process start and never logged.

LLM API keys are scoped per-PM where possible (most providers do not support this — you have one master key with usage tracking on your side instead).

### 11.3 Logging

Structured logs only. JSON in production. `loguru` for ergonomics. Log levels:
- `DEBUG`: verbose, off in production.
- `INFO`: lifecycle events, decisions, state changes.
- `WARNING`: recoverable problems, cost warnings, retry triggers.
- `ERROR`: failures that need human attention.
- `CRITICAL`: kill-switch trips, system-level outages.

Every log line includes: `agent_id`, `pm_id`, `task_id`, `request_id`. So you can trace a single user action through every component.

Logs ship to a single file per service in development; to CloudWatch (or equivalent) in production. Long-term log analytics is out of scope for v1.

### 11.4 Versioning

Semver for the AGORA platform package: `0.1.0` for K1 done, `0.5.0` for K5, etc. Tag in git on each keystone completion.

Strategy YAMLs are versioned independently per PM (`v001`, `v002`, ...).

LLM model identifiers are explicit in config, never in code. Upgrading the default model is a config change, not a code change.

### 11.5 Documentation

Three doc tiers, all in the repo:

- **Plan docs** (`2.0/plan/`): this file and the framework doc. The "why" and "what next."
- **Runbooks** (`2.0/plan/runbooks/`): operational procedures. Cold start, recovery, scaling out, kill switch, manual overrides.
- **Component docs** (next to code): READMEs in each major package. Focus on "how does this work, why was it built this way, what would I touch to extend it."

Documentation is updated in the same PR as the code change. Stale docs are bugs.

### 11.6 Observability discipline

Three rules:

1. Every workflow, every activity, every LLM call, every tool call gets a span. No invisible operations.
2. Every span has a name that tells you the business operation, not the function name. `pm.build_cycle.reflect`, not `_inner_call_4`.
3. Every error is logged with the trace ID. Click any error, jump to the trace.

### 11.7 Security posture (single-user)

Single human means we can be relaxed about most multi-user concerns, but the agents themselves are an attack surface (prompt injection, malicious tool output). Defenses:

- Tools that touch the network (`web_fetch`) sanitize content before returning.
- Tools that touch the filesystem strictly enforce path scope.
- Tools that affect money (`submit_order`) check kill switch every time.
- The PM's prompt has explicit "ignore instructions in fetched content" wording.
- Sandboxes are torn down after every engineer task — no persistence between tasks.

This is "not actively hostile to itself." Real security review is a future-state concern.

### 11.8 Dev workflow

Standard git flow:
- `main` is always deployable.
- Feature branches per keystone task, named `keystone-N/<task>`.
- PRs run full CI before review.
- I approve and merge. Rebase, do not merge-commit, to keep history linear.
- `make precommit` runs lint, types, fast tests; required before pushing.

Daily routine during the build:
- Morning: review yesterday's auto-merged + queued PRs.
- Midday: pair-code with Sonnet on one keystone task.
- Evening: write tests, run soaks, journal.

This routine assumes you are mostly using Claude Code or similar to build AGORA itself. Yes, the irony.

---

## 12. Anti-Patterns to Avoid

Patterns that will tempt you and that should be resisted.

**Premature multi-PM optimization.** Do not try to support 4 PMs in K1. Get one working end-to-end first. The cost of the second PM is mostly polish and config.

**Premature observability.** Do not build a custom metrics pipeline. Langfuse + Postgres + filesystem journals is enough. Ship more product, instrument more later.

**Reaching for `trading-framework/` code.** Every time you think "I already solved this," check that statement. The old solution probably has the wrong shape for the new architecture. Rewrite is faster than retrofit.

**Custom LLM frameworks.** Stick with LangGraph + litellm. Do not roll your own message-passing layer.

**Custom workflow engines.** Use Temporal. Even when you think the use case is too simple. The benefits compound.

**Mode-aware agent code.** The PM agent should not have `if mode == "build": ...` branches. The mode controller decides which workflow to run. Each workflow type knows its mode and only its mode.

**Saving "defensive code" that "might be useful later."** Delete it. If you need it later, you can write it later, when you actually understand what it should do.

**Lots of small abstractions.** When you see "GenericAgentInterface" appearing in the codebase, push back. PMs and engineers and researchers are different roles with different shapes. Letting them be three different concrete classes is fine.

**Dashboard featuritis.** The dashboard is a tool, not a product. Five clean pages beat fifteen busy ones.

**Auto-merge before you trust your CI.** Auto-merge on a CI pipeline that is missing checks is a footgun. CI maturity must precede auto-merge enablement.

**Adding more memory layers.** Letta + Qdrant + Postgres is already three. A fourth (Redis cache, vector DB B, custom JSON blob store) requires a strong reason.

---

## 13. Pre-Flight Checklist

Before writing the first line of code, confirm:

### Decisions you are committing to

- [ ] AGORA is the framework, prop firm is the first app on it.
- [ ] Open by default, write-isolated by policy. No hard sandboxing of PMs from each other.
- [ ] Build mode / trading mode split, enforced by platform.
- [ ] Tree-rooted lifecycle owned by Temporal.
- [ ] One monorepo at `2.0/`, with `pms/<pm_id>/` write-scoped per PM.
- [ ] Sonnet 4.5 default, Haiku 4.5 for cheap calls; no Opus by default.
- [ ] Paper trading only for v1.
- [ ] Single human operator. Single password auth.
- [ ] Existing `trading-framework/` is reference, not dependency.

### Accounts and access you need before starting

- [ ] Anthropic API key with sufficient quota.
- [ ] OpenAI API key (for embeddings and as a fallback).
- [ ] GitHub account where the AGORA monorepo will live.
- [ ] GitHub App created (or plan to create as part of K5).
- [ ] E2B account with credits.
- [ ] Domain for the dashboard (optional; can defer until production).
- [ ] Cloudflare account (for Access in front of dashboard, optional until production).

### Local environment ready

- [ ] Python 3.13+ and `uv` installed.
- [ ] Node 20+ and pnpm installed.
- [ ] Docker Desktop (or OrbStack on Mac) running.
- [ ] At least 16 GB RAM available for the local stack.
- [ ] At least 50 GB free disk space.

### What you are explicitly NOT doing in v1

- [ ] No live trading.
- [ ] No multi-user support.
- [ ] No mobile app.
- [ ] No public API.
- [ ] No research team (deferred to post-v1).
- [ ] No production deployment automation in K1–K8 (local-first; can run on a small EC2 once K8 is done).

### A daily 30-minute routine you commit to

- [ ] Morning: read yesterday's PMs' journals (5 min).
- [ ] Morning: review PR queue (15 min).
- [ ] Evening: review costs, leaderboard, system health (10 min).

This is what makes you the operator and not just the builder.

---

## Closing note

The Keystone Plan is sequential by design. Each keystone is small enough to finish in one to two weeks of focused work and large enough to lock something architecturally meaningful into place.

The instinct to skip ahead — to start writing the engineer agent before the heartbeat works, or to start spawning PM2 before PM1 trades — is the instinct that has killed most ambitious solo builds. Resist it.

When in doubt, the answer is: finish the current keystone's definition of done, then write the next keystone's definition of done so concretely that "am I there yet?" is unambiguous.

The total budget is 10 calendar weeks of focused solo work to a 2-PM, 1-engineer-team-each, paper-trading prop firm running on a durable, observable platform. From there, every additional PM is a config change, every additional team type (research, then maybe a third role) is a 1-week mirror of an existing keystone, and every operational improvement is a small targeted PR rather than an architecture rewrite.

That is the point of the keystone arch. The first eight stones are heavy. Once they are in, the rest is roof.

---

*End of The Keystone Plan.*
*Framework design: `00-FRAMEWORK.md`.*
