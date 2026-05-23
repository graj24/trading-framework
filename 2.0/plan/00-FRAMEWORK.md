# AGORA

> A platform for autonomous agent organizations.
> Where personas compete in public, build their own infrastructure, and remember everything.

---

## Table of Contents

1. [What AGORA Is, In One Page](#1-what-agora-is-in-one-page)
2. [Origin: From `trading-framework/` to AGORA](#2-origin-from-trading-framework-to-agora)
3. [Product Framing: Prop Firm as the First Application](#3-product-framing-prop-firm-as-the-first-application)
4. [Six Guiding Principles](#4-six-guiding-principles)
5. [Mental Model](#5-mental-model)
6. [Architecture Diagrams](#6-architecture-diagrams)
7. [Component Reference](#7-component-reference)
8. [The Trading Firm Application](#8-the-trading-firm-application)
9. [Human-in-the-Loop Interfaces](#9-human-in-the-loop-interfaces)
10. [Hard Architectural Rules](#10-hard-architectural-rules)
11. [Explicitly Out of Scope](#11-explicitly-out-of-scope)
12. [Open Questions Logged for Later](#12-open-questions-logged-for-later)
13. [Glossary](#13-glossary)

---

## 1. What AGORA Is, In One Page

AGORA is a single-tenant (one human operator), multi-persona (many AI agents), 24/7 operating system for autonomous agent organizations. It exists to make one specific thing tractable: **letting AI agents form hierarchical teams that build their own software, accumulate their own knowledge, and compete on measurable outcomes — all under one human's supervision.**

The first product running on AGORA is a **prop trading firm** with multiple Portfolio Manager (PM) agents trading the Indian equity market. But AGORA itself is domain-agnostic. The same primitives — a competition substrate, a build/run split, recursive agent spawning, durable workflows, total observability — would apply equally to a content studio, a research lab, or a software consultancy of agents.

The architectural bet: **isolation is a prompt-and-permissions concern, not a hypervisor concern**. PMs can read everything but write only to their own scope. This is open competition, not adversarial sandboxing, and it collapses an enormous amount of infrastructure complexity.

The operational bet: **agents do not run uniformly 24/7**. They split their day into a trading mode (cheap, deterministic, frozen code) and a build mode (expensive, exploratory, where engineering and research happen). The mode is enforced by the platform, not chosen by the agent.

The product bet: **a single human can supervise this if the system is observable enough**. Every agent decision is a span. Every artifact is a file. Every PR is queued for review with auto-merge for trivial cases. The dashboard is the cockpit.

---

## 2. Origin: From `trading-framework/` to AGORA

The current `trading-framework/` repo is a 6,768-symbol, 226-flow autonomous trading system with working FinBERT, DTW pattern matching, a regime classifier, ML signal models, paper-trade ledger, FastAPI backend, React UI, and a hand-rolled tool-calling strategist. It works. It is also a tangled coexistence of three runtime models on overlapping infrastructure (`core/` and `common/core/`), with manual `setsid` daemon launches, no durable workflow engine, and no real organizational hierarchy among agents.

AGORA is a **clean break**, not a refactor. The existing repo is not a runtime dependency. It is a reference document — read it, take what you need (FinBERT integration, DTW patterns, RAG context shape, the mode-aware scheduler insight), then archive it. The first PM spawned on AGORA inherits behavior by being told "study this repo, replicate the parts that work, do better." Old code becomes the seed, not the substrate.

This is more aggressive than a strangler fig. It's appropriate because:

- There is no real money at stake.
- There are no customers depending on uptime.
- The existing code's value is conceptual, not operational.
- A clean break lets AGORA make choices the old repo couldn't (Temporal, NautilusTrader, write-isolation as policy, build/trading mode split, monorepo).

The cost of a clean break is the few weeks of build time before AGORA is producing trades again. The benefit is an architecture that fits the actual product instead of accreting around an outgrown one.

---

## 3. Product Framing: Prop Firm as the First Application

Phrase it precisely: **AGORA is the framework. The prop firm is the first application on AGORA.** Confusing the two is how this project becomes "another trading bot" instead of a platform.

### What "the prop firm" means concretely

A prop trading firm running on AGORA looks like this:

- One human (you), the operator, with paper money and one or more LLM API budgets.
- One to four PM agents, spawned on demand, each a persona with a system prompt, a memory store, a workspace directory in a monorepo, and a strategy that evolves over time.
- Each PM can recursively spawn sub-teams: an engineering team (a head + N engineers) and a research team (a lead + N researchers).
- All PMs read the same NSE market data. All PMs trade against the same paper broker. All PMs' performance is visible on a single leaderboard.
- All PMs' code, journals, plans, and research notes are visible to each other and to you. Open competition, not isolation.
- The market clock dictates mode. During NSE trading hours, code is frozen and PMs only execute. Outside trading hours, PMs can spawn engineers, write code, do research, evolve strategies, and open PRs.
- You review PRs. Trivial PRs auto-merge after passing CI. Substantial PRs queue for your eyes.
- You can start, stop, pause, and inspect any PM at any time. Starting a PM starts its whole tree. Stopping a PM stops its whole tree.

### What AGORA-the-framework needs to provide

- **Lifecycle**: spawn / stop / pause / resume an agent and its tree.
- **Mode controller**: a single source of truth for "is the firm in trading mode or build mode right now."
- **Sandbox primitive**: when an agent needs to run code, give it an isolated environment.
- **Tool registry**: the things agents can do — file ops, shell, web, market data, broker, memory, sub-agent spawn.
- **Memory layer**: persistent persona, persistent notes, queryable history.
- **Observability**: live stream, historical traces, structured artifacts.
- **Cost controls**: per-agent budgets, soft warnings, hard caps.
- **Human queue**: PR review, alerts, kill switch.

### What the prop firm application provides on top

- The trading domain logic: NautilusTrader strategies, market data adapters, signal generation, risk gates, broker integration.
- The PM persona prompts, the cold-start menu, the leaderboard SQL view.
- Trading-specific tools (`get_quote`, `place_order`, `query_positions`).
- The market hours calendar (NSE-specific) wired into the mode controller.

This separation matters. A future second application — say, a content studio with multiple Editor agents and writer sub-teams — would reuse all of AGORA-the-framework and provide its own domain logic in a parallel directory. We are not building one product. We are building a platform whose first product is the trading firm.

---

## 4. Six Guiding Principles

These are the architectural axioms. Every design choice in AGORA can be derived from them, and any choice that contradicts them is wrong.

### Principle 1: Open by default, write-isolated

Every PM can read every other PM's repo, journal, strategy, memory, and trade history. PMs can write only inside their own workspace. No hypervisor isolation, no per-tenant VPCs, no separate repos. One monorepo, one Postgres, one memory store, namespace-tagged.

**Why:** Strategy is not the moat in trading. Execution, sizing, discipline, and capital are. If PM2 copies PM1's strategy, PM2 still produces different P&L. Public visibility produces better thinking. Debuggability is dramatically easier. Real prop desks already work this way.

**What it costs:** A small amount of philosophical rigor in PM prompts to enforce *cognitive* independence (PM2 should *choose* whether to study, ignore, or counter PM1 — its prompt does not pre-bias it).

### Principle 2: Two modes — build and trading — enforced by the platform

The day splits into two phases:

- **Trading mode** (NSE 09:15–15:30 IST, weekdays, non-holidays): code is frozen, PRs do not merge, no hot reloads. Only the PM execution loop runs. Engineer agents are paused. Token budget is tight.
- **Build mode** (everything else): PMs can spawn engineers and researchers, write code, open PRs, run backtests, evolve strategies. Token budget is loose.

A 09:00 IST cutoff freezes the codebase 15 minutes before market open. Anything not merged by then waits until after 15:30.

**Why:** Code never changes during live trading. PR review fits a human's evening. Costs are bounded — build mode is where token spend explodes, and you only pay it during the ~75% of the week the market is closed. This mirrors how real trading desks operate.

**What it costs:** A clock-driven mode controller and discipline about what runs when. Worth it.

### Principle 3: Tree-rooted lifecycle

When you start a PM, you start its entire downstream tree (any sub-teams it has previously spawned). When you stop a PM, the entire tree stops cleanly. The platform owns the process tree, not the PM.

**Why:** A PM that crashes should not leave orphan engineers running. A human stopping a PM should not have to chase down child processes. Recursive composition with parent ownership is the only sane model.

**What it costs:** A real workflow engine (Temporal). Worth it for everything else Temporal also provides.

### Principle 4: Observability is the product

The dashboard is not a side artifact. It is the only way a single human can supervise a system this complex. Three layers:

- **Live activity stream**: tree of running agents, current LLM call, current tool call, token spend so far.
- **Historical traces**: every LLM call is a span, every tool call is a span (Langfuse).
- **Written artifacts**: journals, plans, PR descriptions, research memos. These are the *narrative*.

If you cannot tell at a glance what every agent is doing, the system is unsafe. Build observability first, not last.

### Principle 5: The single human is the bottleneck — ruthlessly automate review

You will be the only human reviewer. With 4 PMs and 4 engineers each, you could see 50 PRs per day. This is your most fragile resource.

Mitigation:

- Aggressive CI: lint, types, unit tests, contract tests, backtest equivalence checks. PRs that do not pass do not reach you.
- Eng Head triages PRs from its engineers and only forwards approved ones to the human queue.
- Auto-merge for trivial PRs (changes scoped to docstrings, comments, or specific allowlisted files) when CI is green.
- A ranking system: PRs are scored on impact (lines changed, files touched, files in critical paths) and you see highest-impact first.

If review breaks down, autonomy breaks down. Build review tooling like your time depends on it. It does.

### Principle 6: Cost-aware by construction

LLM spend is the dominant cost. A naive setup with 4 PMs and 4 engineers per PM running on Opus 24/7 is plausibly five-figures-USD/month before any signal of whether the system works.

Defaults:

- Sonnet 4.5 for reasoning agents (PM, Eng Head, Research Lead).
- Sonnet 4.5 for engineer agents on non-trivial tasks; Haiku 4.5 for routine code.
- Haiku 4.5 for classification, routing, summaries.
- Per-PM daily token cap. When approached, the PM is notified; when breached, build mode is suspended for that PM.
- Cost dashboard from day one. Per-agent, per-PM, per-day, per-task.

Opus is reserved for cases where Sonnet measurably underperforms and the task is high-stakes. You will likely never need it.

---

## 5. Mental Model

Three planes layered on each other.

```
┌────────────────────────────────────────────────────────────────────┐
│ CONTROL PLANE                                                       │
│   The firm — runs as you.                                           │
│                                                                     │
│   Responsibilities:                                                 │
│   • Spawn / stop / pause / resume PMs                               │
│   • Provision per-PM workspace (directory, memory namespace, repo)  │
│   • Mode arbitration (build vs trading)                             │
│   • Master kill switch                                              │
│   • Cost monitoring + budget enforcement                            │
│   • Human PR review queue                                           │
│   • Cross-PM leaderboard (the only intentional cross-PM signal)     │
│                                                                     │
│   Implementation:                                                   │
│     FastAPI control plane + Postgres state +                        │
│     Temporal supervisor workflows + Next.js dashboard               │
└────────────────────────────────────────────────────────────────────┘
                                │
                                ▼ spawns
┌────────────────────────────────────────────────────────────────────┐
│ TENANT PLANE  (one logical tenant per PM, but shared infra)         │
│                                                                     │
│   Each PM is a tree of agents:                                      │
│     PM Agent (root)                                                 │
│       ├── Trading Loop  (active in trading mode)                    │
│       └── Build Loop    (active in build mode)                      │
│              ├── Engineering Team Head                              │
│              │     └── Engineers × N  (OpenHands in E2B)            │
│              └── Research Team Lead                                 │
│                    └── Researchers × N                              │
│                                                                     │
│   All agents in a PM's tree share that PM's:                        │
│   • workspace directory in the monorepo                             │
│   • memory namespace                                                │
│   • token budget                                                    │
│   • persona (the PM's identity propagates down)                     │
└────────────────────────────────────────────────────────────────────┘
                                │
                                ▼ reads
┌────────────────────────────────────────────────────────────────────┐
│ SHARED INFRASTRUCTURE PLANE                                         │
│                                                                     │
│   Read-write by all PMs (write scoped by namespace):                │
│   • Postgres   — state, ledger, journals                            │
│   • Letta      — per-agent persona/memory                           │
│   • Qdrant     — research notes, namespaced                         │
│   • Object store — large artifacts (model files, datasets)          │
│                                                                     │
│   Read-only by all PMs:                                             │
│   • Market data feed (NSE, news, fundamentals)                      │
│   • Other PMs' workspaces (open competition)                        │
│   • Other PMs' memory namespaces                                    │
│                                                                     │
│   Write-isolated:                                                   │
│   • Each PM writes only to /pms/<pm_id>/* in the monorepo           │
│   • Each PM writes only to its own memory namespace                 │
│   • Engineering PRs target the monorepo but are scoped to /pms/<id>/│
└────────────────────────────────────────────────────────────────────┘
```

The "tenant plane" is logical, not physical. Two PMs share the same processes, the same database, the same filesystem. The tenancy is enforced by:

1. Tool-level path checks (PM2's `write_file` cannot target paths outside `/pms/pm2/`).
2. CI-level path checks (PRs from PM2's engineers fail if they touch files outside `/pms/pm2/`).
3. Memory-level namespace tags (PM2 cannot write to namespace `pm1`).

This is "write isolation by policy," not by sandbox. It is enforceable by code review, by CI, and by tool-call validation. It is sufficient for the open-competition product model.


---

## 6. Architecture Diagrams

### 6.1 System view

```
┌──────────────────────────────────────────────────────────────────────┐
│                          AGORA OPERATOR (you)                         │
│                                                                       │
│   Browser  ──▶  Next.js dashboard  ──▶  FastAPI control plane         │
│                       │                       │                       │
│                       │ websocket             │ REST                  │
│                       ▼                       ▼                       │
│                ┌──────────────────────────────────────────┐           │
│                │           CONTROL PLANE SERVICE          │           │
│                │  • PM lifecycle endpoints                │           │
│                │  • PR review queue                       │           │
│                │  • Mode arbitration                      │           │
│                │  • Budget + kill switch                  │           │
│                │  • Inspection / debug                    │           │
│                └──────────────┬───────────────────────────┘           │
│                               │                                       │
│                               │ start / stop signals                  │
│                               ▼                                       │
│                ┌──────────────────────────────────────────┐           │
│                │              TEMPORAL SERVER             │           │
│                │  • Per-PM supervisor workflows           │           │
│                │  • Per-task engineer workflows           │           │
│                │  • Per-task research workflows           │           │
│                │  • Heartbeat / cron schedules            │           │
│                └──────────────┬───────────────────────────┘           │
│                               │                                       │
│                               │ activities                            │
│                               ▼                                       │
│   ┌───────────────────────────────────────────────────────────┐       │
│   │                  AGENT WORKERS (Python)                    │      │
│   │   • PM Agent worker                                        │      │
│   │   • Eng Head worker                                        │      │
│   │   • Engineer worker (OpenHands wrapper)                    │      │
│   │   • Research Lead worker                                   │      │
│   │   • Researcher worker                                      │      │
│   │                                                            │      │
│   │   All workers:                                             │      │
│   │   • Use litellm for LLM calls                              │      │
│   │   • Emit Langfuse traces                                   │      │
│   │   • Read/write via the tool registry                       │      │
│   │   • Subject to per-PM budget                               │      │
│   └───────┬────────────────────────────────────────────────────┘      │
│           │                                                           │
│           ▼                                                           │
│   ┌───────────────────────────────────────────────────────────┐       │
│   │                       TOOL REGISTRY                       │       │
│   │   filesystem  shell  market_data  broker  memory          │       │
│   │   web_fetch   sql    spawn_agent  pr      sandbox         │       │
│   └───┬───────────┬────────┬──────────┬─────────┬─────────────┘       │
│       │           │        │          │         │                     │
│       ▼           ▼        ▼          ▼         ▼                     │
│   ┌────────┐ ┌────────┐ ┌─────────┐ ┌──────┐ ┌──────────┐             │
│   │Postgres│ │  E2B   │ │NautilusT│ │Letta │ │ Langfuse │             │
│   │+monorepo│ │sandbox │ │ paper  │ │+Qdrant│ │  traces  │            │
│   └────────┘ └────────┘ └─────────┘ └──────┘ └──────────┘             │
└──────────────────────────────────────────────────────────────────────┘
```

### 6.2 Lifecycle view (one PM, full day)

```
06:00 ─────────────────────────────────────────────────────────────────▶
       BUILD MODE

       PM Agent active. Reviewing yesterday's trades.
       May spawn engineers, researchers.
       PRs may merge if CI passes + reviewed.

09:00 ─── FREEZE CUTOFF ──────────────────────────────────────────────▶

       No more PRs merge. Active engineer tasks suspended.
       PM Agent transitions to trading mode.

09:15 ─── MARKET OPEN ────────────────────────────────────────────────▶
       TRADING MODE

       Trading Loop runs:
       • Read market data
       • Generate signals (using frozen code)
       • Apply risk gates
       • Place orders via NautilusTrader
       • Update positions

       No code changes. No engineer spawns. No research.
       Cost is low (mostly deterministic, few LLM calls).

15:30 ─── MARKET CLOSE ───────────────────────────────────────────────▶

       Trading Loop saves final state.
       PM Agent re-enters build mode.

15:30+ BUILD MODE

       PM Agent journals the day.
       PM Agent reflects: what worked, what did not.
       PM may spawn engineers or researchers based on reflection.
       Engineer/research output queues up for tomorrow's freeze cutoff.

       Loop until 09:00 next trading day.
```

### 6.3 Data flow (one engineer task)

```
PM Agent decides: "I want a Bollinger Bands indicator."
   │
   ▼
PM calls tool: spawn_engineer_task(spec="Add BB indicator to /pms/pm1/strategies/")
   │
   ▼
Control plane creates Temporal workflow: EngineerTaskWorkflow(task_id, spec)
   │
   ▼
Workflow activity: provision E2B sandbox, clone monorepo, checkout new branch
   │
   ▼
Workflow activity: run OpenHands with task spec, scoped to /pms/pm1/
   │  (OpenHands writes code, runs tests, iterates)
   ▼
Workflow activity: run AGORA CI inside sandbox (lint, types, unit tests,
                   path-scope check, backtest equivalence)
   │
   ▼
   ┌─── if CI fails ─────────────────────────────────────────────┐
   │   Activity: send failure summary back to PM Agent's journal │
   │   Workflow ends                                             │
   └─────────────────────────────────────────────────────────────┘
   │
   ▼ (CI passed)
Workflow activity: open PR via GitHub App (target: monorepo, scoped to /pms/pm1/)
   │
   ▼
Workflow activity: Eng Head reviews the PR
   │
   ┌─── if Eng Head rejects ─────────────────────────────────────┐
   │   Activity: append review to PR, close it                   │
   │   Workflow ends                                             │
   └─────────────────────────────────────────────────────────────┘
   │
   ▼ (Eng Head approved)
Workflow activity: enqueue PR for human review (or auto-merge if trivial)
   │
   ▼
Human reviews on dashboard, clicks merge.
   │
   ▼
Monorepo updated. PM Agent's next build cycle picks up the new code via git pull.
```


---

## 7. Component Reference

### 7.1 Control plane

**Tech:** FastAPI (Python) + Postgres + Next.js dashboard.

**Responsibilities:**

- Public HTTP API for the human operator.
- Authenticates the operator (single-user password + Cloudflare Access in front).
- Spawns / stops PM supervisor workflows in Temporal.
- Provisions per-PM workspace (directory in the monorepo, memory namespace, GitHub permissions).
- Maintains the PR review queue (poll GitHub, augment with metadata, surface in dashboard).
- Owns the kill switch (a row in Postgres; checked by every order before placement).
- Owns the mode controller (a deterministic function of clock + holiday calendar + manual overrides).

**Endpoints (skeleton):**

```
POST  /pms/spawn                  — { name, prompt_file, capital, llm_model } → pm_id
POST  /pms/{pm_id}/stop           — graceful stop, cascades to tree
POST  /pms/{pm_id}/pause          — pause without stopping
POST  /pms/{pm_id}/resume
GET   /pms/{pm_id}                — full state
GET   /pms                        — list all
GET   /mode                       — current mode (build|trading)
POST  /mode/override              — manual override (with expiry)
GET   /prs                        — PR queue
POST  /prs/{id}/merge
POST  /prs/{id}/reject
GET   /budget                     — costs
POST  /kill-switch/activate
POST  /kill-switch/deactivate
GET   /agents/{agent_id}/trace    — Langfuse trace ref
WS    /stream                     — live activity stream
```

**State (Postgres tables, simplified):**

```
pms              (id, name, status, spawned_at, prompt_path, llm_model, ...)
agents           (id, pm_id, parent_agent_id, role, status, started_at, ...)
runs             (id, agent_id, started_at, ended_at, tokens_in, tokens_out, cost_usd)
tasks            (id, agent_id, kind, spec, status, started_at, completed_at)
prs              (id, agent_id, github_pr_number, status, summary, ...)
budget_events    (id, pm_id, ts, kind, amount_usd, ...)
mode_overrides   (id, requested_by, mode, expires_at, ...)
```

### 7.2 Mode controller

A deterministic function with three inputs:

```python
def current_mode(now: datetime, calendar: HolidayCalendar, overrides: list[Override]) -> Mode:
    if any active override:
        return override.mode
    if not calendar.is_trading_day(now.date()):
        return BUILD
    if now.time() < time(9, 0):       # before freeze cutoff
        return BUILD
    if now.time() >= time(9, 15) and now.time() < time(15, 30):
        return TRADING
    if now.time() >= time(9, 0) and now.time() < time(9, 15):
        return PRE_TRADE_FREEZE        # interim mode: no PRs merge, no new engineer spawns
    return BUILD                       # after 15:30
```

Three modes, not two — `PRE_TRADE_FREEZE` is the 15-minute window where the codebase is locked but trading has not started. This catches the edge case of "a PR almost merging at 09:14:55."

The mode controller is published over the event bus. Every component (PM workers, engineer workers, PR auto-merger) subscribes and reacts.

### 7.3 Temporal supervisors

Each PM is a Temporal workflow. The workflow is the lifecycle. When you start a PM, you start its workflow. When you stop, you signal the workflow to terminate.

```python
@workflow.defn
class PMSupervisor:
    @workflow.run
    async def run(self, pm_id: str, config: PMConfig):
        await activity.execute(setup_pm_workspace, pm_id, config)
        try:
            while not self.stop_signal:
                mode = await activity.execute(get_current_mode)
                if mode == TRADING:
                    await activity.execute(run_trading_loop, pm_id)
                else:
                    # build mode — this is where the PM may spawn child workflows
                    await activity.execute(run_build_loop, pm_id)
                # short sleep before re-evaluating mode
                await workflow.sleep(timedelta(seconds=30))
        finally:
            await activity.execute(teardown_pm, pm_id)
```

Sub-team workflows (Engineer Task, Research Task) are child workflows of the PM supervisor. When the supervisor terminates, children terminate via Temporal's parent-close policy.

The durability of Temporal solves three problems at once: tree-rooted lifecycle, resume-after-crash, and replayable trace of what happened.

### 7.4 PM Agent

The PM is a LangGraph supervisor agent. It has two modes of operation, switched by the controller signal.

**Trading mode (one cycle, repeated every 60 seconds during market hours):**

```
1. Fetch latest market state for watchlist
2. Compute signals using frozen strategy code at /pms/<pm_id>/strategies/active
3. For each candidate trade:
   - Apply risk gates (NautilusTrader risk engine + PM-specific overrides)
   - If passed, submit order to paper broker
4. Update positions, journal the cycle
```

This loop is mostly deterministic. The LLM is invoked rarely — for ambiguous cases or when the strategy explicitly asks for a judgment call.

**Build mode (cycle, repeated at PM's chosen cadence — e.g., hourly during off-hours):**

```
1. Read state (plan, journal, recent trades, leaderboard, market context)
2. Read rivals (other PMs' journals, strategies, recent trades — open competition)
3. Reflect via LLM: what worked, what did not, what to do next?
4. Choose action:
   • DO_NOTHING
   • UPDATE_PLAN
   • SPAWN_ENGINEER     (delegate to engineering team)
   • SPAWN_RESEARCHER   (delegate to research team)
   • EVOLVE_STRATEGY    (commit a new strategy version directly)
   • PIVOT              (major strategy shift)
5. Execute action (publish events, write files, spawn child workflows)
6. Journal the cycle
```

The PM does not write code itself. It delegates code-writing to engineers. It does not do deep research. It delegates to researchers. It does plan, decide, and integrate.

### 7.5 Engineering team

Each PM may spawn an Eng Head agent. The Eng Head is created lazily — first time the PM wants to delegate engineering work, the Eng Head is provisioned. Once provisioned, it persists across cycles.

**Eng Head responsibilities:**

- Receives engineering tasks from the PM.
- Decomposes tasks into engineer-scoped sub-tasks.
- Spawns engineer workflows (one workflow per sub-task).
- Reviews engineer PRs internally before forwarding to the human queue.
- Maintains an engineering log (decisions, rejected approaches, technical debt accumulated).

**Engineer (a worker, ephemeral per task):**

- One Temporal workflow per task.
- Wraps an OpenHands instance running in an E2B sandbox.
- Sandbox is provisioned with:
  - The monorepo cloned, on a fresh branch.
  - Read access to the entire monorepo (including other PMs).
  - Write access only to `/pms/<pm_id>/*`.
  - The PM's API keys for LLM and market data, scoped via short-lived tokens.
- Engineer iterates on the task, runs tests, eventually opens a PR.
- Sandbox is torn down when the task completes.

**Why OpenHands and not roll-your-own:** OpenHands is the strongest open-source autonomous SWE agent (50%+ on SWE-Bench Verified). It already handles the messy parts: file editing, multi-file refactors, running tests, iterating on failures. We are not in the business of beating it. We wrap it.

### 7.6 Research team

Mirror of engineering, but produces written artifacts instead of code.

**Research Lead:**

- Receives research tasks from the PM.
- Decomposes into researcher-scoped sub-tasks.
- Spawns researcher workflows.
- Reviews researcher reports internally.

**Researcher (worker):**

- One Temporal workflow per task.
- Has tools: `web_search`, `web_fetch`, `read_file`, `sql_query`, `memory_store`, `memory_search`.
- Produces a written report at `/pms/<pm_id>/research/<task_id>/report.md` plus citations.
- Report is also stored in the PM's memory (Letta archival) tagged for future search.

The point of the research team is to produce *durable knowledge* the PM can cite later. Not just "go look something up once" but "build a body of work this PM can draw on."

### 7.7 Memory layer

Three components, layered:

**Letta (per-agent persona memory):**

- One Letta agent per AGORA agent (PM, Eng Head, Researcher, etc.).
- Hierarchical memory: core (always-in-context, slow-to-change persona facts), recall (recent conversation), archival (long-term store).
- This is what makes the PM "remember it is PM2 with a counter-PM1 strategy" across restarts and model swaps.

**Qdrant (per-PM research notes):**

- One namespace per PM: `pm1`, `pm2`, etc.
- Researchers write embedded notes here. PMs query when reasoning.
- Cross-PM read is allowed (open competition); cross-PM write is forbidden.

**Postgres (structured state):**

- Trade ledger, journals, plans, PR queue, agent runs.
- The system of record for anything queryable as a relational table.

The decision rule:

- "Who am I and how do I think?" → Letta.
- "What have I learned about the market?" → Qdrant.
- "What did I do?" → Postgres.

### 7.8 Trading core

**NautilusTrader.** Production-grade, Rust-backed, async, multi-venue. We do not roll this. We use it.

NautilusTrader provides:

- Strategy abstraction (your strategy is a Python class with `on_bar`, `on_trade_tick`, etc.).
- Order management (limit, market, stop, trailing — handled by the engine, not your strategy).
- Risk engine with built-in pre-trade checks.
- Backtesting that uses the *same* strategy code as live (eliminates the four-divergent-backtester problem).
- Paper trading via a simulated venue.

For NSE specifically, NautilusTrader does not ship a Zerodha adapter. We will write a `ZerodhaVenue` adapter when we go live. For paper trading + research, the simulated venue is sufficient.

PMs have access to NautilusTrader through the `broker` and `market_data` tools. They do not interact with NautilusTrader's internals; they submit orders and query state through tool calls.

### 7.9 Repo strategy

**One monorepo.** Directory layout:

```
agora/
├── platform/              ← framework code (control plane, workers, tools, etc.)
│   ├── control_plane/
│   ├── workers/
│   ├── tools/
│   ├── memory/
│   ├── observability/
│   └── ...
├── apps/
│   └── propfirm/          ← the trading application
│       ├── strategies/    ← shared strategy primitives
│       ├── data/          ← market data adapters
│       ├── risk/          ← risk gates
│       └── prompts/       ← PM persona prompt templates
├── pms/                   ← per-PM workspaces (engineer write target)
│   ├── pm1/
│   │   ├── strategies/
│   │   │   ├── v001.yaml
│   │   │   ├── v002.yaml
│   │   │   └── ACTIVE     → v002.yaml
│   │   ├── plans/
│   │   ├── journals/
│   │   ├── research/
│   │   └── code/          ← PM-specific code (engineer-generated)
│   ├── pm2/
│   └── ...
├── tests/                 ← framework tests
├── ci/                    ← AGORA CI scripts (path scope check, etc.)
├── pyproject.toml
└── README.md
```

**Engineer write scope:** PRs from PM1's engineers must touch only `/pms/pm1/**`. CI enforces this with a path-scope check that fails any PR violating the rule. This is the technical enforcement of "write isolation by policy."

**GitHub App:** Single GitHub App with per-PM tokens. Each engineer's PR is opened under a bot account scoped to that PM (`agora-pm1-bot`, `agora-pm2-bot`, etc.).

### 7.10 LLM routing

**litellm** as the unified interface. Wrapped in an AGORA-specific layer that:

- Tags every call with agent_id, pm_id, task_id (for cost attribution).
- Routes by capability: reasoning calls go to Sonnet, code calls go to Sonnet or Haiku, classification goes to Haiku.
- Enforces per-PM budget — when a PM is at 90% of daily budget, calls are throttled; at 100%, build mode is suspended.
- Emits Langfuse spans on every call.

Default routing table (overridable per-agent):

```yaml
reasoning_pm:        anthropic/claude-sonnet-4-5
reasoning_eng_head:  anthropic/claude-sonnet-4-5
reasoning_research:  anthropic/claude-sonnet-4-5
engineering_complex: anthropic/claude-sonnet-4-5
engineering_simple:  anthropic/claude-haiku-4-5
classification:      anthropic/claude-haiku-4-5
summarization:       anthropic/claude-haiku-4-5
embeddings:          openai/text-embedding-3-small
```

### 7.11 Observability

**Three layers, three tools.**

**Langfuse** for LLM and tool-call traces. Self-hosted. Every LLM call emits a span. Every tool call emits a span. Spans are nested under task spans, which are nested under agent spans, which are nested under PM spans. Click any span to see prompt, response, latency, cost.

**Postgres + structured logs** for system events. Agent lifecycle (started, stopped, paused), task lifecycle (created, completed, failed), PR lifecycle (opened, reviewed, merged). Everything queryable in SQL.

**Filesystem artifacts** for narrative content. Journals, plans, research reports, PR descriptions. Markdown files committed to the monorepo.

**Dashboard live stream:** the dashboard subscribes to a websocket from the control plane. Every agent state change pushes a message. The UI renders a tree, color-coded by status. Click a node to see its current LLM call (streaming), its current tool call, recent journal entries.

### 7.12 Cost / governance

**Per-PM daily budget:** configured in `/pms/<pm_id>/config.yaml`. Default $20/day for build mode, $5/day for trading mode (trading mode rarely uses LLM).

**Budget tracker:** every LLM call's cost is recorded in `budget_events`. The control plane exposes a query: `current_pm_spend(pm_id, date)`. Each LLM call hits this before submitting.

**Soft warning:** at 80% of budget, the PM is notified via a journal entry and an event bus message. The PM may decide to wind down voluntarily.

**Hard cap:** at 100%, build mode is suspended for that PM until midnight IST. The trading loop continues (it has its own tiny budget).

**Master kill switch:** a row in Postgres. Checked by every broker call. Toggled from the dashboard. When active, no orders go through, all PMs continue running but cannot trade.


---

## 8. The Trading Firm Application

This section is what the prop firm — running on AGORA — looks like in practice.

### 8.1 PM persona prompt structure

Every PM has a prompt at `/apps/propfirm/prompts/<pm_id>.md`. The prompt is composed of three layers:

**Layer 1 — TEMPLATE.md (shared by all PMs):**

```
You are PM{pm_id}. You are an autonomous portfolio manager running inside AGORA.

THE GAME:
You are competing against other PMs to generate the highest cumulative P&L
on a shared paper-trade ledger. There is no other objective.

YOUR FREEDOM:
- You may study any other PM's code, strategy, journal, and trade history.
- You may not modify other PMs' files.
- You may spawn engineers to write code in your workspace.
- You may spawn researchers to investigate any topic.
- You may rewrite your own strategy at any time during build mode.
- You cannot modify code during trading mode.

YOUR RESPONSIBILITIES:
- Plan: maintain /pms/{pm_id}/plans/current.md
- Journal: append to /pms/{pm_id}/journals/{date}.md every cycle
- Strategy: keep /pms/{pm_id}/strategies/ACTIVE pointing at your current strategy
- Reflect: review your performance daily, spawn work to improve

THE MODE:
The platform tells you whether it's trading mode or build mode.
You do not choose. Respect the mode.
```

**Layer 2 — PM-specific identity (`PM1.md`, `PM2.md`):**

```
You are PM1. You inherit a working strategy from /apps/propfirm/seed_strategies/momentum_v1.yaml.

STARTING DISPOSITION:
- Your starting capital is ₹1,000,000 (paper).
- You may evolve the seed strategy as you see fit.
- You should establish your identity within the first week (a name, a style, a thesis).

OBSERVATIONS ABOUT THE WORLD:
- Indian equities, NSE, long-only, no F&O.
- Market hours 09:15–15:30 IST.
- Liquidity is good for NIFTY 50, decent for NIFTY 500, thin for small caps.
```

**Layer 3 — current state (auto-injected at runtime):**

```
CURRENT MODE: build
TIME: 22:14 IST, Friday
LEADERBOARD:
  PM1 (you): ₹1,032,400 (+3.24% over 14 days, 12 trades, 58% win rate)
  PM2:       ₹1,011,200 (+1.12% over 7 days, 4 trades, 75% win rate)

YOUR PLAN (last updated 2 days ago):
  ... (read from /pms/pm1/plans/current.md)

YESTERDAY'S TRADES:
  ... (read from Postgres, last 24h)
```

### 8.2 Cold-start menu (for spawned PMs)

When you spawn PM3, the platform injects this on its first build cycle:

```
You are a brand new PM. You have no strategy, no history, no journal.

You have four options for how to begin. Choose one:

A. START BLANK — Invent your own strategy from scratch.
   Use web_search and web_fetch to research approaches.
   Write your initial strategy at /pms/pm3/strategies/v001.yaml.

B. INHERIT — Read the working strategies of other PMs (you have full read
   access). Write a modified version at /pms/pm3/strategies/v001.yaml.

C. RESEARCH FIRST — Spend a week studying other PMs' trade histories,
   the market, and academic literature. Store findings via memory_store.
   Begin trading only after you have a thesis.

D. COUNTER — Identify another PM's weaknesses by reading their journal
   and trade history. Build a strategy designed to outperform when they
   underperform.

Make your choice and write your reasoning to /pms/pm3/journals/cold_start.md.
```

The choice is not pre-committed in the prompt — the PM agent makes it.

### 8.3 Trading mode loop (per-PM)

Pseudocode for the trading mode cycle, executed every 60 seconds during market hours:

```python
async def trading_loop(pm_id: str):
    pm = await load_pm(pm_id)
    strategy = await load_active_strategy(pm_id)   # frozen at 09:00
    market = await market_data.snapshot(pm.watchlist)

    signals = strategy.generate_signals(market)

    for signal in signals:
        if not pre_trade_checks(pm, signal):
            continue
        risk_check = nautilus_risk_engine.check(signal)
        if not risk_check.ok:
            await journal(pm_id, f"REJECTED: {signal.symbol}: {risk_check.reason}")
            continue
        order = build_order(signal, pm.capital, pm.position_sizer)
        result = await broker.submit(order, pm_id=pm_id)
        await journal(pm_id, f"PLACED: {order.summary()}")
        await record_trade(pm_id, order, result)
```

Notes:

- The strategy is loaded once at the start of trading mode (09:15) and stays frozen until 15:30.
- The PM agent's LLM is rarely invoked during trading mode — only if the strategy explicitly delegates a judgment call.
- All orders go through NautilusTrader's risk engine, then through AGORA's broker tool, which checks the kill switch and PM-specific gates.

### 8.4 Build mode loop (per-PM)

Pseudocode for the build mode cycle, executed every hour during off-hours:

```python
async def build_loop(pm_id: str):
    pm = await load_pm(pm_id)
    state = await read_state(pm_id)               # plan, journal, positions, leaderboard, rivals
    rivals = await read_rivals(pm_id)             # other PMs' journals + strategies + trades

    decision = await pm.llm.reflect(state, rivals)
    # decision: { action: ..., reasoning: ..., args: ... }

    if decision.action == "DO_NOTHING":
        await journal(pm_id, "Reflection: no action this cycle.")
        return

    if decision.action == "SPAWN_ENGINEER":
        eng_head = await get_or_spawn_eng_head(pm_id)
        task_id = await eng_head.delegate(decision.args.task_spec)
        await journal(pm_id, f"Spawned engineer task {task_id}: {decision.args.task_spec}")

    if decision.action == "SPAWN_RESEARCHER":
        # ... similar
        ...

    if decision.action == "EVOLVE_STRATEGY":
        # The PM writes a new strategy version directly (small changes).
        new_version = await commit_strategy_version(pm_id, decision.args.yaml)
        await journal(pm_id, f"Evolved strategy to {new_version}")

    if decision.action == "UPDATE_PLAN":
        await write_plan(pm_id, decision.args.plan_md)
        await journal(pm_id, "Updated plan.")
```

### 8.5 Leaderboard

A SQL view (refreshed on every trade close):

```sql
CREATE VIEW leaderboard AS
SELECT
    pm_id,
    starting_capital,
    SUM(pnl_inr) AS realized_pnl,
    starting_capital + SUM(pnl_inr) AS current_capital,
    100.0 * SUM(pnl_inr) / starting_capital AS pct_return,
    COUNT(*) FILTER (WHERE outcome != 'open') AS closed_trades,
    100.0 * SUM(CASE WHEN pnl_inr > 0 THEN 1 ELSE 0 END)
        / NULLIF(COUNT(*) FILTER (WHERE outcome != 'open'), 0) AS win_rate_pct
FROM trades
GROUP BY pm_id, starting_capital
ORDER BY pct_return DESC;
```

This view is read by the dashboard, by every PM at the start of every cycle (open competition), and by the cost dashboard for "is this PM earning its keep?" queries.

---

## 9. Human-in-the-Loop Interfaces

You have four primary interfaces with the system.

### 9.1 The dashboard (Next.js, ~10 pages)

```
/                      Overview — leaderboard, mode, kill switch, cost today
/pms                   PM list — start/stop/pause buttons
/pms/[id]              PM detail — tree of agents, current activity, journal
/pms/[id]/strategy     Active strategy YAML, version history, diff view
/pms/[id]/journal      Append-only journal, scrollable
/pms/[id]/research     Research reports library
/agents/[id]           Agent detail — current LLM call, recent traces
/prs                   PR queue — diff view, CI status, eng-head review, merge button
/budget                Cost — by PM, by agent, by day, by model
/admin                 Mode override, kill switch, manual interventions
```

### 9.2 PR review

The dashboard's `/prs` page is your most-used surface. Each PR card shows:

- PM and engineer that produced it.
- One-line task spec.
- Diff (lines added / removed / files).
- CI status (lint / types / unit tests / path scope / backtest equivalence).
- Eng Head review note (the agent's own analysis).
- Auto-merge eligibility (yes/no with reason).
- Three buttons: **Merge**, **Reject**, **Request changes**.

Reject sends a reason back to the engineer, which the PM journals and may re-spawn with adjusted spec.

### 9.3 Live activity stream

The `/pms/[id]` page shows a real-time tree:

```
PM1 ─ thinking (sonnet-4.5, 2.3s)
 ├── EngHead ─ reviewing PR #42
 │    ├── Engineer-task-7 ─ DONE (PR #42 opened)
 │    └── Engineer-task-8 ─ thinking (haiku-4.5, 14s)
 └── ResearchLead ─ idle
```

Click any node to see its current state in detail. Tokens spent so far. Last 5 LLM calls. Last 10 tool calls. Current task description. Time spent.

### 9.4 Inspection (the firehose)

For debugging, you need raw access. Provided through:

- **Langfuse UI** at a separate subdomain — full trace tree of every LLM call ever.
- **SQL access** to Postgres — read-only credentials available.
- **Filesystem access** to the monorepo — you can `cd pms/pm1/journals/` and read.
- **Temporal Web UI** — see workflow state, history, replay.

Three of these (Langfuse, Temporal, Postgres) come free with their respective tools.

---

## 10. Hard Architectural Rules

These are the rules that, if violated, break the system in expensive ways. They are not aesthetic preferences. Treat them as inviolable.

1. **No code change during trading mode.** The mode controller blocks PR merges and engineer spawns from 09:00 to 15:30 IST on trading days. No exceptions, not even "small" changes.
2. **Engineers write only inside their PM's workspace.** Enforced by CI path-scope check. PRs that touch other paths are auto-rejected.
3. **PMs can read everything but write only to their own scope.** Enforced by tool-level path checks and Letta namespace tags.
4. **Tree-rooted lifecycle.** Stopping a PM cascades to its entire tree. No orphan children. Owned by Temporal, not by application code.
5. **Every LLM call has a budget.** No untracked LLM calls. Every call is tagged and accounted for in `budget_events`.
6. **Every action is observable.** No silent decisions. If an agent did something, there is a span, a journal entry, or both.
7. **The PM agent does not write code.** Code is written by engineers in sandboxes. The PM's only direct file writes are journals, plans, and strategy YAML.
8. **The platform owns the mode, not the agent.** Agents query the mode; they do not declare it.
9. **One source of truth per piece of state.** Trade ledger lives only in Postgres. Strategy lives only in YAML on disk. Persona lives only in Letta. No duplication.
10. **The kill switch is honored before every order, every time.** Including by the PM agent's own diagnostic test orders.

---

## 11. Explicitly Out of Scope

Things AGORA does NOT do, deliberately, in v1:

- **Hard tenant isolation.** No per-PM VPCs, no per-PM Postgres, no separate compute. Open by default, write-isolated by policy.
- **Multi-user operator support.** Single human. Auth is a password.
- **Live trading with real money.** Paper only until the architecture has run for months without surprises.
- **Multi-region deployment.** Single EC2 (or single Mac mini at home). Vertical scaling only.
- **Streaming market data with sub-100ms latency.** This is a swing/positional system. NSE 1-minute bars are sufficient.
- **Internal agent-to-agent direct messaging.** Agents communicate via files (journals, plans, PRs) and structured events. Not free-form chat.
- **General-purpose plugin marketplace.** Tools are added to the registry by you, in code.
- **Web UI for editing prompts.** Prompts live in the repo, edited as files, version-controlled.
- **Public-facing API.** AGORA is your firm. There are no customers.

These can become in-scope later. Until they do, do not build for them.

---

## 12. Open Questions Logged for Later

Listed not because they need answers now, but so we do not pretend they are answered.

- **Engineer task isolation level.** OpenHands in E2B with the monorepo cloned is the plan. If E2B feels too heavyweight or too restrictive, fall back to local Docker containers per task. Decide at implementation time based on cost and DX.
- **What happens when two PMs have conflicting orders for the same symbol at the same instant?** Both go through. Paper market. Real life would have nuance.
- **Strategy version garbage collection.** PM might commit hundreds of versions over time. Cap retained versions to last N + all-time-best M? Defer.
- **Backtesting fidelity for engineer-written strategies.** The engineer must run a backtest before opening a PR, but our backtest equivalence harness is not yet defined. Critical for trust in autonomy.
- **What is the right time horizon for PMs to develop a track record before we trust them with real money?** This is partly architectural (do we need a "promotion gate" for PMs?) and partly philosophical (what would even count as "trustworthy"?). Defer until we have data.
- **Multi-asset extension.** NSE-only is fine for v1. If we expand to crypto or US equities, NautilusTrader supports both, but the data adapters and risk profiles diverge.
- **Compliance / audit trail.** Currently we have logs and journals. For real money under SEBI rules, we need structured audit. Not now.

---

## 13. Glossary

- **AGORA** — the framework. Greek marketplace. The platform on which agent organizations run.
- **Application** — a domain-specific layer on AGORA. The prop firm is the first.
- **PM (Portfolio Manager)** — a top-level agent in the prop firm app. Has a persona, workspace, capital, leaderboard rank.
- **Eng Head, Engineer, Research Lead, Researcher** — sub-agents under a PM. Each is its own LangGraph or OpenHands worker, with its own memory.
- **Workspace** — `/pms/<pm_id>/` directory in the monorepo. The PM's writable scope.
- **Memory namespace** — the PM's namespace in Letta and Qdrant. Other PMs read it; only this PM writes to it.
- **Build mode / Trading mode / Pre-trade freeze** — the three operating modes, governed by the mode controller.
- **Mode controller** — the deterministic function (clock + holidays + overrides → mode) that drives system-wide mode.
- **Tree-rooted lifecycle** — the property that starting a PM starts its whole tree, and stopping a PM stops its whole tree.
- **Supervisor workflow** — a Temporal workflow representing a PM's lifecycle. Owns child workflows for sub-tasks.
- **Engineer task / Research task** — a child Temporal workflow spawned by the PM (via Eng Head or Research Lead) to do bounded work.
- **Path-scope check** — CI rule that fails any PR touching paths outside the engineer's PM workspace.
- **Auto-merge eligibility** — a PR property indicating CI is green, the diff is small, and the path scope is the PM's own non-critical files. Auto-merge fires after a configurable delay if no human action.
- **Kill switch** — a global flag, checked by every order, that blocks order submission when active.
- **Cold-start menu** — the four-option prompt presented to a newly spawned PM on its first build cycle.
- **Leaderboard** — the SQL view ranking PMs by realized P&L. The only intentional cross-PM signal.
- **Open competition** — the architectural choice that PMs see each other's full state. Differentiation comes from different decisions, not different information.
- **Write isolation by policy** — the enforcement model. Tool checks + CI checks + namespace tags. No hypervisor.

---

*End of AGORA framework design.*
*The implementation plan lives at `01-KEYSTONE.md`.*
