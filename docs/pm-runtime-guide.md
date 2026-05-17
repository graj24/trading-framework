# PM Runtime Guide

The **PM Runtime** is the always-on, event-driven layer that turns the framework into
something resembling a real fund: each Portfolio Manager (PM1, PM2, …) acts like a human
PM — reacting to market events in real time, delegating to sub-agents, and maintaining
state across the trading day.

Everything below was added in the `feat: event-driven PM runtime + PMs monitoring page`
commit and is independent of (but composes with) the original signal-driven pipeline
documented in [`technical-reference.md`](technical-reference.md).

---

## 1. Architecture at a glance

```
┌────────────────────────────────────────────────────────────────────────────┐
│  Tier 1 — Publishers (deterministic, no LLM)                               │
│    intraday_scanner  →  price.spike.<SYMBOL>                               │
│    news_agent        →  news.<SYMBOL>                                      │
│    position_monitor  →  fill.<pm_id>                                       │
└─────────────────────────────────┬──────────────────────────────────────────┘
                                  ↓ writes
                          ┌──────────────────┐
                          │   events.db      │   SQLite WAL pub/sub
                          │   (event bus)    │
                          └────────┬─────────┘
                                   ↓ subscribed by
┌──────────────────────────────────┴──────────────────────────────────────────┐
│  Tier 2 — Standing daemons (per PM, hot work, long-lived Python procs)      │
│                                                                             │
│   PM<N>.Triage   ──→ classifies events (rule fast-path → Llama 8B)          │
│                      • ignore                                               │
│                      • exec   → publishes exec_order.<pm_id>                │
│                      • wakeup → publishes pm.wakeup.<pm_id>                 │
│                      • research → publishes research.<pm_id>                │
│                                                                             │
│   PM<N>.Trader   ──→ subscribes exec_order.<pm_id>, runs deterministic      │
│                      pre-trade gates, places order via broker abstraction,  │
│                      publishes fill.<pm_id>                                 │
│                                                                             │
│   PM<N>.Risk     ──→ polls every 30 s — refreshes positions, runs           │
│                      circuit-breaker + VaR, publishes risk.breach.<pm_id>   │
│                      and pm.wakeup.<pm_id> on breaches                      │
└─────────────────────────────────────────────────────────────────────────────┘
                                   │
                                   ↓ pm.wakeup events
┌──────────────────────────────────┴──────────────────────────────────────────┐
│  Tier 3 — Strategic PM (Multica)                                            │
│    PM<N>  reads workspace (plan, journal, inbox, positions) and delegates   │
│    long-form work to PM<N>.Researcher / PM<N>.Trader / PM<N>.Risk via       │
│    Multica issues. Wakes on heartbeat (6×/day) or on a Triage escalation.   │
└─────────────────────────────────────────────────────────────────────────────┘
```

**Why three tiers?** The hot path (sub-second reactions) must never block on an LLM.
Tier 1 publishers are pure Python. Tier 2 daemons use a tiny Llama-8B classifier and
otherwise run deterministic SQL-backed checks. Only Tier 3 — strategic decisions over
seconds-to-minutes — engages the heavyweight reasoning model via Multica.

---

## 2. Components reference

### 2.1 Event bus — `core/event_bus.py`

SQLite-backed pub/sub. WAL mode, file-locked, polled by subscribers.

```python
from core.event_bus import get_bus

bus = get_bus()                                          # module-level singleton
bus.publish("price.spike.RELIANCE", {"pct": 1.4},        # topic, payload
            pm_id=None, severity="WARNING")              # optional metadata

# Polling consumer
cursor = 0
while True:
    rows = bus.poll(since_id=cursor, topic_prefix="price.spike", limit=100)
    for row in rows:
        handle(row)
        cursor = row["id"]
    time.sleep(0.5)
```

**Topic conventions** (case-sensitive, dot-separated):

| Topic                    | Producer                | Consumer                     |
|--------------------------|-------------------------|------------------------------|
| `price.spike.<SYMBOL>`   | `intraday_scanner`      | every PM's Triage            |
| `news.<SYMBOL>`          | `news_agent`            | every PM's Triage            |
| `fill.<pm_id>`           | `pm_trader` / monitor   | PM Risk, PM (Tier 3)         |
| `exec_order.<pm_id>`     | `pm_triage`             | `pm_trader.<pm_id>`          |
| `research.<pm_id>`       | `pm_triage`             | PM Researcher (Tier 3)       |
| `pm.wakeup.<pm_id>`      | scheduler / `pm_risk` / `pm_triage` | PM (Tier 3)        |
| `risk.breach.<pm_id>`    | `pm_risk` / `risk_manager` | PM (Tier 3)               |
| `system.daemon.<pm_id>`  | every daemon on start   | observability                |
| `system.pm.<pm_id>`      | API (pause/resume)      | observability                |

Wildcards: subscribers can pass `topic_prefix="risk."` to receive everything under
the `risk` namespace.

DB file: `events.db` at the repo root. Inspect with:
```bash
.venv/bin/python -c "
import sqlite3, json
for r in sqlite3.connect('events.db').execute('SELECT id, ts, topic, severity FROM events ORDER BY id DESC LIMIT 20'):
    print(r)
"
```

---

### 2.2 PM runtime — `core/pm_runtime.py`

Manages the registry of which PMs exist.

```python
from core.pm_runtime import register_pm, list_pms, get_pm_config, deactivate_pm

register_pm(pm_id="3", workspace="pm_3", prompt_path="pm_prompts/PM3_full_prompt.md")
list_pms(active_only=True)
get_pm_config("1")
deactivate_pm("2")
```

Persists to `pm_registry.json` at the repo root.

---

### 2.3 PM workspace — `core/pm_state.py`

Each PM owns a directory at `pm_<id>/`:

```
pm_<id>/
├── state/
│   ├── plan.md                 # Read-only in v1 — the PM's standing plan
│   ├── tasks.yaml              # Backlog / in_progress / done
│   ├── journal.md              # Decisions, hypothesis tracking
│   ├── journal_summary.md      # Compressed summary
│   ├── journal_archive/        # Older entries
│   ├── inbox.jsonl             # Unread events queued by Triage
│   ├── positions.json          # Mirror of broker positions
│   ├── proposals.jsonl         # Trade ideas from sub-agents
│   ├── team.yaml               # Sub-agent registry
│   ├── triage_cursor.txt       # Last event id consumed by Triage
│   ├── trader_cursor.txt       # Last event id consumed by Trader
│   ├── triage_decisions.jsonl  # Every Triage classification (for the UI)
│   └── PAUSED                  # Sentinel file — pause/resume control
├── agents/                     # Sub-agent prompt overrides (optional)
└── config.yaml                 # Per-PM overrides (capital, sectors, …)
```

The helpers in `core/pm_state.py` (`read_plan`, `read_tasks`, `read_journal`,
`push_inbox`, `read_positions`, …) are the only interface anything else should use.

---

### 2.4 Standing daemons — `agents/pm_triage.py`, `pm_trader.py`, `pm_risk.py`

All three follow the same shape:

```bash
.venv/bin/python -m agents.pm_triage --pm_id 1
.venv/bin/python -m agents.pm_trader --pm_id 1
.venv/bin/python -m agents.pm_risk   --pm_id 1
```

Each:
- writes a per-daemon log to `logs/pm<N>_<daemon>.log`
- persists its event-bus cursor in `pm_<id>/state/<daemon>_cursor.txt`
  so a restart resumes exactly where it stopped
- publishes a `system.daemon.<pm_id>` `start` event for observability
- exits cleanly on SIGTERM

**Triage** uses two-stage classification:
1. Rule fast-path: drops `price.spike` < 0.5 %, off-PM events, etc.
2. Llama 3.1 8B: `ignore` / `exec` / `wakeup` / `research`

Every classification (rule or LLM) is appended to `triage_decisions.jsonl`.

**Trader** runs deterministic gates before placing every order:
- kill switch
- per-PM `PAUSED` sentinel
- `risk_manager.check_circuit_breaker(pm_id)`
- broker rate-limits

**Risk** polls every 30 s. Silent on success, logs only warnings/errors.

---

### 2.5 Broker safety — `core/broker.py`

Two safety layers shared by `PaperBroker` and `ZerodhaBroker`:

| Mechanism            | File / API                                                       |
|----------------------|------------------------------------------------------------------|
| Kill switch          | `KILL_SWITCH_PATH` (default `KILL_SWITCH`) + `activate_kill_switch()` / `deactivate_kill_switch()` / `is_kill_switch_active()` |
| Global rate limiter  | 30 orders/min total, 10 orders/min per PM (in-memory token bucket) |

Every order from any path runs through these. The kill switch file is the
single source of truth — if it exists, all orders are blocked and an audit
entry is written.

---

### 2.6 Risk circuit breaker — `agents/risk_manager.py`

```python
from agents.risk_manager import check_circuit_breaker, audit_log

allowed, reason = check_circuit_breaker("1")             # reads paper_trades.db
audit_log("1", "ORDER_BLOCKED", {"symbol": "RELIANCE", "reason": reason})
```

Halt rules (configurable in `config.yaml` under `risk:`):
- daily realised loss ≤ `−max_loss_per_day_pct` → halt PM, kill_switch path
- weekly realised loss ≤ `−max_loss_per_week_pct` → halve sizes
- breach publishes `risk.breach.<pm_id>` (severity `CRITICAL`)

Audit trail: every event appends one JSON line to `risk_audit.jsonl`.

---

### 2.7 Schema migrations — `core/migrations.py`

Idempotent ALTER TABLEs run on import. Currently:
- adds `pm_id TEXT DEFAULT '1'` to `paper_trades.db.trades`
  (existing trades inherit PM1)

Imported by `risk_manager`, `pm_state`, and the PMs API router so any code path
that touches the DB ensures the schema is current.

---

### 2.8 Heartbeat scheduler — `core/scheduler.py`

Six cron jobs (IST), each calls `job_pm_heartbeat(shift)` →
`_multica_wakeup(pm_id, shift)` for every active, non-paused PM:

| Cron (IST) | Shift name      |
|------------|-----------------|
| 08:30      | `pre_market`    |
| 09:15      | `open`          |
| 11:00      | `mid_morning`   |
| 12:30      | `lunch`         |
| 14:00      | `afternoon`     |
| 15:30      | `close`         |

`_multica_wakeup` POSTs a Multica issue with `build_wakeup_context()` as the body;
falls back to an event-bus `pm.wakeup.<pm_id>` publish if `MULTICA_TOKEN` is missing.

All heartbeat / preopen / pre-market / execute jobs are also gated on
`core.holidays.is_trading_day` (no-ops on weekends and NSE holidays).

---

## 3. The `/pms` observability page

Route: **`/pms`** in the React UI.

| Region              | Component                                  | What it shows                                       |
|---------------------|--------------------------------------------|-----------------------------------------------------|
| Top bar             | `PMs.tsx` header                           | Replay toggle, kill-switch button, page title       |
| Left rail           | `PMCard` × N                               | Per-PM card: P&L, open positions, paused/active dot |
| Centre              | `react-flow` canvas                        | Tier-1 → events.db → Triage / Trader / Risk → broker. Edges *animate* when matching events arrive over the WS |
| Bottom strip        | event ticker                               | Last 30 events, color-coded by topic                |
| Right drawer        | `DetailPanel` with 8 tabs                  | Plan / Journal / Tasks / Inbox / Trades / Audit / Triage / Trace |

**Wire-up**:
- REST: `/api/pms`, `/api/pms/{pm_id}/state`, `/api/pms/{pm_id}/audit`,
  `/api/pms/{pm_id}/triage_log`, `/api/pms/{pm_id}/trades`,
  `/api/pms/events`, `/api/pms/events/latest_id`,
  `/api/pms/kill_switch{,/activate,/deactivate}`,
  `/api/pms/{pm_id}/{pause,resume,paused}`
- WebSocket: `/ws/pm_events` — pushes every new `events.db` row with
  `{type:"pm_event", event:{...}}`. Send `{type:"seek", from_id:N}` to replay
  from a past event id.

---

## 4. Operations

### 4.1 Provisioning a new PM (PM3, PM4, …)

```bash
# 1. Author the PM's prompt
cp pm_prompts/PM2.md pm_prompts/PM3.md     # edit mandate, capital, sectors
cat pm_prompts/TEMPLATE.md pm_prompts/PM3.md > pm_prompts/PM3_full_prompt.md

# 2. Provision workspace + Multica sub-agents
.venv/bin/python scripts/register_pm.py \
    --pm_id 3 \
    --prompt pm_prompts/PM3_full_prompt.md

# 3. Start daemons
for D in pm_triage pm_trader pm_risk; do
  setsid .venv/bin/python -m agents.$D --pm_id 3 </dev/null \
    >> logs/pm3_${D#pm_}.log 2>&1 &
done
```

**Multica integration:** `register_pm.py` looks up the workspace and a `kiro` runtime
via the Multica REST API, then creates `PM<N>.Researcher`, `PM<N>.Trader`, and
`PM<N>.Risk` agents. Required env: `MULTICA_TOKEN` (workspace access token from the
Multica UI), `MULTICA_SERVER_URL`. Without these, sub-agents skip; the local
workspace is still provisioned.

### 4.2 Pausing / resuming a PM

```bash
# UI: click the ⏸ Pause PM button in the right drawer of /pms

# CLI:
curl -X POST 'http://13.206.3.62/api/pms/1/pause?reason=manual%20review'
curl -X POST 'http://13.206.3.62/api/pms/1/resume'
curl 'http://13.206.3.62/api/pms/1/paused'
```

Pausing writes `pm_<id>/state/PAUSED`. While present:
- Trader blocks all orders for that PM
- Triage suppresses `exec_order` routing (still logs decisions, still pushes inbox)
- Heartbeat scheduler skips that PM's wakeup

### 4.3 Kill switch (all PMs, all orders)

```bash
# UI: red button in /pms top bar
# CLI:
curl 'http://13.206.3.62/api/pms/kill_switch'
curl -X POST 'http://13.206.3.62/api/pms/kill_switch/activate'
curl -X POST 'http://13.206.3.62/api/pms/kill_switch/deactivate'
```

When active, the file `KILL_SWITCH` exists at the repo root and *every* broker call
returns immediately with an audit log entry.

### 4.4 Restarting daemons

Daemons are intentionally not under `systemd` yet — they're launched as detached
Python processes. To restart cleanly:

```bash
pkill -9 -f 'agents.pm_'
sleep 2
cd /app
for PM in 1 2; do
  for D in pm_triage pm_trader pm_risk; do
    setsid .venv/bin/python -m agents.$D --pm_id $PM </dev/null \
      >> logs/pm${PM}_${D#pm_}.log 2>&1 &
  done
done
```

Each daemon resumes from its persisted cursor — no events are lost or duplicated
across restarts.

### 4.5 Smoke test

```bash
# Inject a synthetic price spike and watch the flow on /pms
.venv/bin/python -c "
from core.event_bus import get_bus
get_bus().publish('price.spike.RELIANCE',
    {'symbol':'RELIANCE','pct_change':1.2,'price':2500},
    severity='WARNING')
"
```

Within ~1 s the event appears on the ticker, Triage classifies, the canvas edge
pulses, and the Trace tab grows by one row.

---

## 5. Files added / changed

| File                            | Purpose                                                |
|---------------------------------|--------------------------------------------------------|
| `core/event_bus.py`             | SQLite WAL pub/sub                                     |
| `core/pm_state.py`              | Workspace read/write helpers                           |
| `core/pm_runtime.py`            | PM registry                                            |
| `core/migrations.py`            | Idempotent schema migrations                           |
| `core/broker.py`                | Kill switch + global/per-PM rate limiter               |
| `core/scheduler.py`             | Tier-1 publishers, PM heartbeat shifts, holiday guards |
| `agents/pm_triage.py`           | Triage daemon                                          |
| `agents/pm_trader.py`           | Trader daemon                                          |
| `agents/pm_risk.py`             | Risk daemon                                            |
| `agents/risk_manager.py`        | Circuit breaker + audit log                            |
| `api/routers/pms.py`            | `/api/pms/*` endpoints                                 |
| `api/routers/ws.py`             | `/ws/pm_events` WebSocket                              |
| `frontend/src/pages/PMs.tsx`    | The `/pms` page                                        |
| `frontend/src/lib/api.ts`       | Typed client for the new endpoints                     |
| `scripts/register_pm.py`        | One-shot provisioning script                           |
| `pm_prompts/PM1.md`, `PM2.md`   | Updated with 24/7 runtime contract                     |

---

## 6. What's deliberately not built yet

- No live-money path. `ZerodhaBroker.place_order` is wired through the same gates,
  but `execute_trades.live_mode` defaults to false. Promote in `config.yaml` only
  after several days of paper-mode signal stability.
- Tier 2 daemons run as plain Python procs, not under `systemd`. Acceptable while
  the user manually monitors; promote to `systemd` units before stepping away.
- Multica sub-agents are *optional collaborators* today — Triage / Trader / Risk
  daemons run the deterministic hot path; the Multica researcher / trader / risk
  agents do strategic work invoked via Multica issues from Tier 3.
- Replay supports forward-only seek; no time-window scrubber yet.
