# AGORA

## What is AGORA

AGORA is a platform for autonomous agent organizations: long-lived AI agents that plan, build, and operate software for a domain — starting with a competitive prop trading firm where multiple Portfolio Managers evolve their own strategies and engineering teams. The full design lives in [`plan/00-FRAMEWORK.md`](plan/00-FRAMEWORK.md).

## Current state

Keystone 1 in progress (Step 1.1: repo bootstrap). The full keystone plan is in [`plan/01-KEYSTONE.md`](plan/01-KEYSTONE.md). Until later steps land, most `make` targets (`up`, `api`, `worker`, `dashboard`, `db-migrate`, `smoke-llm`) are stubs that exit non-zero on purpose.

## Quickstart

```bash
cp .env.example .env       # then edit; no secrets are required for Step 1.1
uv sync --all-groups
make ci-local              # lint + typecheck + test
```

For Step 1.1 specifically, no environment variables need real values yet — the smoke test does not call any external service. The placeholders in `.env.example` document what later steps will need.

## Layout note

The keystone plan §3 Step 1.1 sketches `platform/` at the repo root. We use `src/agora/platform/` instead, because `platform` collides with the Python stdlib module of the same name and causes import-time ambiguity. The boundary the plan cares about — framework code vs application code — is preserved as `agora.platform.*` vs `agora.apps.*`.

```
2.0/
├── src/agora/
│   ├── platform/           # framework: control plane, workers, tools, memory, llm, observability, shared
│   └── apps/               # applications: propfirm (and future siblings)
├── dashboard/              # Next.js (placeholder until Step 1.8)
├── pms/                    # per-PM workspaces, populated at runtime
├── tests/
├── ci/, infra/             # populated in later steps
├── plan/                   # design + keystone plan
├── pyproject.toml          # uv-managed
└── Makefile
```

CI lives at the repo root in `.github/workflows/agora-ci.yml`, scoped to changes under `2.0/**`. GitHub Actions only auto-discovers workflows at the repo root, not at sub-directory `.github/` folders.
