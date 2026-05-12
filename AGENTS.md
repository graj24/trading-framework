# Agent Instructions — Trading Framework

Read this first before doing any work on this project. It captures project context, conventions, current phase, and hard constraints.

## Project Purpose

Autonomous trading framework for **Indian equities (NSE)**, targeting **maximum absolute returns** via an agent-based architecture. Long-term vision is a multi-strategy system that switches based on market regime (trending_bull / trending_bear / ranging / high_volatility).

- **Current strategy style**: Intraday swing — enter on daily signals, exit within 1-5 days.
- **Target strategy style**: Multi-strategy (regime-routed: trend-following, mean-reversion, shorts via futures, options overlay).
- **Broker**: Groww API (real broker with orders + quotes).
- **Capital runway**: Starting ₹10K for validation, scaling to ₹10L+ once profitable baseline is proven.
- **Execution mode**: Paper trading now, fully automated live trading as the goal.

## Current Phase

See `docs/specs/2026-05-12-phased-roadmap-design.md` for the full roadmap.

**Active phase: Phase 0 — Critical fixes** (~2-3 weeks).
Objective: Make the intraday-swing strategy production-ready on real data before any live capital is deployed.

## Hard Constraints

- **Max drawdown: 20%** — system pauses, strategy reviewed. Do not build anything that violates this without explicit approval.
- **Paper mode only** until Phase 2 go-live is explicitly approved by the user.
- **Long-only equity cash** until Phase 3 adds shorts via futures.
- **Indian market hours (IST)** — NSE 09:15-15:30. All scheduling and logic must respect this.
- **Transaction cost realism** — STT, exchange charges, stamp duty, GST must be modeled. See `agents/execution_agent.py` for current simplified model.

## Documentation Convention

All analysis, learnings, tasks, plans, and design specs live under `docs/`. See `docs/README.md` for structure.

**Always save to `docs/` before the conversation ends:**
- New framework analyses → `docs/analysis/YYYY-MM-DD-<topic>.md`
- Learnings from trades, research, or debugging → `docs/learnings/YYYY-MM-DD-<topic>.md`
- Design docs before implementation → `docs/specs/YYYY-MM-DD-<feature>-design.md`
- Improvement ideas / future work → append to `docs/plans/IMPROVEMENTS.md` or create focused file
- Task-level notes for in-progress work → `docs/tasks/YYYY-MM-DD-<task>.md`

If the user says "take a note" or "save this for later," write it to the appropriate `docs/` subfolder — don't leave it in conversation only.

## Project Conventions

- **Agent architecture**: Single-responsibility agents in `agents/`, orchestrated by `agents/master.py`. Follow existing `Agent`/`AgentResult` abstractions from `agents/base.py`.
- **Knowledge base per stock**: `stocks/<SYMBOL>/` with fundamentals, correlations, patterns, event reactions, signal weights. Read via `core/knowledge_base.py`.
- **Config**: `config.yaml` at root. Capital, risk limits, schedule, watchlist.
- **Data source priority**: Groww API first, yfinance as fallback. Don't add new yfinance-only paths.
- **Secrets**: `.env` file, never commit. Use `python-dotenv`.
- **Logging**: `core/logger.py` — always `logger.info/warning/error`, not `print`, in production code.

## Coding Conventions

- Python 3.11+, type hints where meaningful, `from __future__ import annotations` at the top of modules.
- Follow existing patterns — don't introduce new frameworks or libraries without discussion.
- Use `dotenv`, `pandas`, `numpy`, `yfinance`, `litellm`, `apscheduler` — already in `requirements.txt`.
- When adding new dependencies, pin the version and explain the choice.

## Testing Conventions

- No formal test framework set up yet. If adding tests, use `pytest` (standard choice). Put tests under `tests/<agent_or_module>/`.
- Before claiming work complete, run the relevant agent's `__main__` block or `python main.py --once` to verify no regressions.

## Workflow Conventions

- **Before creative work** (new features, behavior changes): use the `brainstorming` skill. Don't jump to code.
- **Before any implementation plan has code**: use `test-driven-development` when applicable.
- **Before claiming completion**: use `verification-before-completion`. Run verification commands, confirm output.
- **If a bug appears**: use `systematic-debugging` before proposing fixes.
- **When multiple independent tasks** exist: use `dispatching-parallel-agents` or `subagent-driven-development`.

## Communication Style

- The user prefers direct, technical language. No filler acknowledgments.
- When making recommendations, explain reasoning.
- Correct the user when they're wrong — honest disagreement is more valuable than agreement.
- Match response format to task: simple questions get direct answers, not headers and bullet points.

## Things to Never Do Without Explicit Approval

- Enable live trading mode (`config.yaml` `trading.mode = live`).
- Push to main / master branch.
- Force-push, reset --hard, or any destructive git operation.
- Deploy to a remote server.
- Modify risk limits in `config.yaml` (daily/weekly/monthly loss caps).
- Delete the `paper_trades.db` or knowledge base data.
