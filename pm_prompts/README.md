# Portfolio Manager Prompts

Each file in this folder is the system prompt / identity document for one Portfolio Manager (PM) agent.

## What is a PM?

A PM is an LLM agent that autonomously manages a paper trading portfolio on the Indian equity market (NSE). Each PM has:
- Its own strategy (simple or complex — entirely up to the PM)
- Access to the full codebase, all agents, all data, and the broker abstraction
- Full freedom to create new agents, install packages, or build a completely different stack
- One goal: make more money than the other PMs

## Current PMs

| PM | File | Strategy summary |
|---|---|---|
| PM1 | `PM1.md` | Multi-signal pipeline: technical + FinBERT + DTW patterns + regime + 2 ML models + LLM arbitration |
| PM2 | `PM2.md` | Competes against PM1 — full freedom to exploit PM1's weaknesses |

## Adding a new PM

1. Create `PM<N>.md` in this folder
2. Give it an identity, show it the other PMs' strategies, and set it loose
3. Each PM should have its own entry point (e.g. `pm2/main.py`) and config
4. All PMs write to the same `paper_trades.db` — P&L is tracked per PM via a `pm_id` tag

## Scoreboard

```bash
sqlite3 paper_trades.db "
  SELECT reasoning LIKE '%PM%' as pm, COUNT(*) trades,
         SUM(pnl_inr) total_pnl
  FROM trades WHERE outcome != 'open'
  GROUP BY 1
  ORDER BY total_pnl DESC;
"
```
