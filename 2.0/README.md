# AGORA

## What is AGORA

AGORA is a platform for autonomous agent organizations: long-lived AI agents that plan, build, and operate software for a domain — starting with a competitive prop trading firm where multiple Portfolio Managers evolve their own strategies and engineering teams. The full design lives in [`plan/00-FRAMEWORK.md`](plan/00-FRAMEWORK.md). The implementation plan, broken into eight sequential keystones, lives in [`plan/01-KEYSTONE.md`](plan/01-KEYSTONE.md).

## Current state

Keystone 1 — Foundation — is complete. The empty platform skeleton is up: Postgres + Temporal + Qdrant + Letta via docker-compose, FastAPI control plane with `/api/health` / `/api/mode` / `/api/pms`, mode controller (build / pre_trade_freeze / trading), Temporal worker + hello workflow, AgoraLLM client wrapping litellm and Langfuse Cloud with per-call budget recording, and a Next.js dashboard at localhost:3000.

Keystone 2 — Heartbeat (a spawnable PM that proves lifecycle works) — is next.

## Quickstart

```bash
# 1. Configure secrets. Only Anthropic + Langfuse Cloud keys are required
#    for the K1 LLM smoke. Postgres / Temporal / Qdrant / Letta are local.
cp .env.example .env

# 2. Sync Python and Node.
uv sync --all-groups
make dashboard-install

# 3. Bring up local infra (Postgres, Temporal, Qdrant, Letta).
make up

# 4. Run database migrations.
make db-migrate

# 5. Start the control plane and the dashboard in separate terminals.
make api          # FastAPI on :8000
make dashboard    # Next.js on :3000
make worker       # Temporal worker on task_queue=agora

# 6. Sanity checks.
make health                  # service health JSON
uv run agora-cli hello world # hello workflow round-trip
make smoke-llm               # one Sonnet 4.5 call → Langfuse trace + budget row
```

CI runs lint + types + tests on every PR touching `2.0/**`. Locally:

```bash
make ci-local
```

## Verifying Keystone 1

The keystone plan defines five "Definition of done" items for K1. After running the quickstart, all five are observable:

| # | Check | How to verify |
|---|---|---|
| 1 | `make up` brings up Postgres, Temporal, Langfuse, FastAPI, Next.js, Letta, Qdrant | `docker compose -f infra/docker-compose.yml ps` shows postgres / temporal / temporal-ui / qdrant / letta healthy. Langfuse is hosted (Langfuse Cloud), so it is not in compose — see "Drift from the plan" below. |
| 2 | localhost:3000 shows empty AGORA dashboard with `PMs (0)` and `PRs (0)` placeholders | Open the dashboard. The header shows the current mode and 0 PMs running; the PRs card is a static placeholder until K5. |
| 3 | `curl /api/health` returns `{"status": "ok", "services": {...}}` listing all services | `curl -s localhost:8000/api/health \| jq .` |
| 4 | A throwaway hello-world Temporal workflow runs end-to-end and shows up in Temporal Web UI | `make worker` in one terminal, `uv run agora-cli hello world` in another. Then open Temporal UI at localhost:8088. |
| 5 | A throwaway LLM call (litellm to Sonnet) appears as a Langfuse span | `make smoke-llm`. The script prints the Langfuse trace URL; open it. The accompanying `budget_events` row is visible via the `psql` one-liner the script prints at the end. |

The end-to-end integration test `tests/test_e2e_workflow_with_budget.py` ties items 4 and 5 together in code: one Temporal workflow calls AgoraLLM (with a stubbed completion) and asserts the budget row is written. Run with `pytest -m integration`.

## Drift from the plan

These deviations from `plan/01-KEYSTONE.md §3` are deliberate and documented in commit messages:

- **`src/agora/platform/`** rather than `2.0/platform/` at the repo root, because `platform` collides with the Python stdlib module of the same name. The framework-vs-application boundary the plan cares about is preserved as `agora.platform.*` vs `agora.apps.*`.
- **Langfuse is hosted (cloud.langfuse.com), not self-hosted.** Compose drops Langfuse + ClickHouse + Redis. Saves ~3 GB of local memory and removes the most fragile piece of the local stack. The control plane's health check still reports Langfuse status from configured keys.
- **Image tag bumps** to match what's actually published on Docker Hub: `pgvector/pgvector:pg16` instead of `postgres:16-alpine` (Letta needs the `vector` extension), `letta/letta:0.16.8` instead of `0.5.0`, `temporalio/ui:2.40.1` instead of `2.40`. Pins are explicit so bumps are deliberate.
- **NSE 2026 holiday list** is hard-coded with a TODO to verify against the official NSE circular before any live trading. Cross-checked against published dates; final verification is an operator drill.
- **CI workflow** lives at the repo root `.github/workflows/agora-ci.yml`, scoped to `2.0/**`. Sub-directory `.github/` folders are not auto-discovered by GitHub Actions.

## Layout

```
2.0/
├── src/agora/
│   ├── platform/
│   │   ├── control_plane/   # FastAPI app, mode controller, alembic migrations
│   │   ├── workers/         # Temporal worker entry, hello workflow
│   │   ├── llm/             # AgoraLLM, cost computation, budget recorder
│   │   ├── memory/          # (Letta wrappers — K4)
│   │   ├── tools/           # (PM-callable tools — K4)
│   │   ├── observability/   # loguru config, request-id middleware
│   │   ├── shared/          # Settings (pydantic-settings)
│   │   └── cli.py           # agora-cli (tyro)
│   └── apps/
│       └── propfirm/        # (NautilusTrader strategies — K3)
├── dashboard/               # Next.js 15 + Tailwind v4 + TanStack Query
├── pms/                     # per-PM workspaces, populated at runtime (K2+)
├── tests/                   # pytest; integration tests gated by `-m integration`
├── ci/                      # AGORA-specific CI scripts (path scope check — K5)
├── infra/                   # docker-compose.yml, postgres init.sql
├── scripts/                 # smoke_llm.py and friends
├── plan/                    # framework + keystone plan
├── pyproject.toml           # uv-managed Python deps
└── Makefile
```
