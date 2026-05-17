# Documentation Index

This folder contains everything written about the **Autonomous Trading Framework**.

There are two flavours:
- **Official docs** — clean references suitable for new users and integrators.
- **Internal analysis** — opinionated, technical, point-in-time notes for the developer (you).

---

## Read first

| If you want to…                                | Start here                             |
|------------------------------------------------|----------------------------------------|
| Install and run the system                     | [`user-guide.md`](user-guide.md)       |
| Understand the architecture / extend modules   | [`technical-reference.md`](technical-reference.md) |
| Learn how a single trade decision is made      | [`analysis/04-decision-pipeline.md`](analysis/04-decision-pipeline.md) |
| See the system at a glance (diagrams)          | [`analysis/02-data-flow.md`](analysis/02-data-flow.md) |
| Find known bugs / pick up TODOs                | [`analysis/05-issues.md`](analysis/05-issues.md) |
| Plan the next sprint                           | [`analysis/06-improvements.md`](analysis/06-improvements.md) |

---

## All documents

### Official

| Document                                       | Audience          | What it covers                                                  |
|------------------------------------------------|-------------------|-----------------------------------------------------------------|
| [`user-guide.md`](user-guide.md)               | End users         | Install, configure, run, dashboard, backtest, ML, troubleshoot, FAQ, glossary |
| [`technical-reference.md`](technical-reference.md) | Engineers      | Architecture, module APIs, config schema, storage schemas, scheduler, ML pipeline, ops |

### Internal analysis (`analysis/`)

| Document                                                       | What it covers                                                                                |
|----------------------------------------------------------------|-----------------------------------------------------------------------------------------------|
| [`analysis/01-architecture.md`](analysis/01-architecture.md)   | Layered system view, god-nodes, control flows, storage model, design choices, what's missing |
| [`analysis/02-data-flow.md`](analysis/02-data-flow.md)         | Mermaid flowcharts: system, decision pipeline, scheduler timeline, KB build, fallback path  |
| [`analysis/03-agents.md`](analysis/03-agents.md)               | Per-agent deep dive — purpose, public methods, constants, gotchas (15+ agents)               |
| [`analysis/04-decision-pipeline.md`](analysis/04-decision-pipeline.md) | Step-by-step walkthrough of `MasterAgent.run_for_stock`, with code references         |
| [`analysis/05-issues.md`](analysis/05-issues.md)               | Bugs, security issues, design smells, performance hotspots — severity-ranked                |
| [`analysis/06-improvements.md`](analysis/06-improvements.md)   | Prioritised roadmap: P0 (this week) → P3 (research). Each item maps back to issues.          |

---

## Reading order suggestions

### "I'm a new user, just installed the repo"
1. [`user-guide.md`](user-guide.md) — sections 1–5.
2. Run `python main.py`.
3. Open the dashboard: `streamlit run scripts/dashboard.py`.
4. Come back to `user-guide.md` §6–9 when ready.

### "I want to extend / modify the framework"
1. [`technical-reference.md`](technical-reference.md) §1–5 (overview + module APIs).
2. [`analysis/01-architecture.md`](analysis/01-architecture.md) — opinionated context.
3. [`analysis/03-agents.md`](analysis/03-agents.md) — pick the agent you'll change.
4. [`analysis/04-decision-pipeline.md`](analysis/04-decision-pipeline.md) — verify your change won't break the pipeline.
5. [`analysis/05-issues.md`](analysis/05-issues.md) — check whether the bug you're fixing is already documented.

### "I'm reviewing this codebase to evaluate it"
1. [`analysis/01-architecture.md`](analysis/01-architecture.md) §1–2 (5-minute read).
2. [`analysis/02-data-flow.md`](analysis/02-data-flow.md) §1–3 (the diagrams).
3. [`analysis/05-issues.md`](analysis/05-issues.md) — focus on the 🔴 / 🟠 entries.
4. [`analysis/06-improvements.md`](analysis/06-improvements.md) §P0 / P1 — what's known to be planned.

### "I'm onboarding to the project"
1. [`user-guide.md`](user-guide.md) — run it once locally.
2. [`analysis/01-architecture.md`](analysis/01-architecture.md) §3–6 (god nodes, control flows, storage).
3. [`technical-reference.md`](technical-reference.md) §3–6 (repo layout, config, modules, storage).
4. Run `python test_stock.py RELIANCE` and read the verbose output.
5. Now you can navigate the rest of the docs by topic.

---

## Companion artefacts (not in this folder)

- [`../graphify-out/GRAPH_REPORT.md`](../graphify-out/GRAPH_REPORT.md) — auto-generated knowledge graph of the codebase. Run `graphify update .` after code changes.
- [`../graphify-out/graph.html`](../graphify-out/graph.html) — interactive knowledge graph (open in a browser).
- [`../config.yaml`](../config.yaml) — runtime configuration (the schema is documented in `technical-reference.md` §4).

---

## Doc maintenance

These docs were generated from a one-shot analysis of the codebase. They go stale fast if not maintained. Two heuristics:

1. **When you change an agent's interface or constants**, update `analysis/03-agents.md` and `technical-reference.md` §5.
2. **When you fix a 🔴 / 🟠 issue from `analysis/05-issues.md`**, delete the entry (or move to a `RESOLVED` section).

Mermaid diagrams in `analysis/02-data-flow.md` render natively on GitHub and most Markdown viewers. If you edit the code paths they describe, update the diagrams too.
